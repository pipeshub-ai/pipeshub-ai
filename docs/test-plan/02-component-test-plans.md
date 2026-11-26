# Master test plan: component test plans

**Status:** plan, 2026-10-09. One section per component of [01](01-component-map.md), in the map's order:
behaviours, the levels used, numbered cases (`<ID>-nn`), the fakes and contract suites, what already exists
(mapped to real files and test names, checked against the code at `7e356c8f2`), and the gaps (`<ID>-Gn`, ranked
by impact × likelihood). Levels are those of [00](00-scope-and-strategy.md) §3. The ranked list across all
components is in [05](05-traceability-and-gaps.md) §3.

Conventions:

- **Exists** cites `file::test` when a test covers the case, **partial: file** when a test exists but misses the
  point, and **new** otherwise. Paths are repo-relative.
- Control-plane components (CAT, POL, CST, PIN, AUD, NTF, CPA) are target design. Their cases are written now
  against [`docs/control-plane/03-interfaces.md`](../control-plane/03-interfaces.md) and run when the CPD WP named
  in the section lands; where today's code already does part of the job, those cases cite today's tests.
- Isolation-runtime cases (gVisor, Kata, K8s agent-sandbox) are written and marked with the lane that will run them.
- Updated after merging integration `a168c7d5c`, which brought CPS (R2 F3–F7, F13 flags, F16 router scopes): the
  cases CPS now covers cite its tests, and the gaps it closed say so in their last column.

**Totals:** 40 components, 827 cases, 248 gaps.

| Component | Name | Cases | Gaps |
|---|---|---|---|
| [CAT](#cat-control-plane-catalog) | Control-plane catalog | 13 | 5 |
| [POL](#pol-policy-engine-dimensions-precedence-narrowing) | Policy engine (dimensions, precedence, narrowing) | 24 | 6 |
| [CST](#cst-control-plane-stores-and-write-transaction) | Control-plane stores and write transaction | 17 | 7 |
| [PIN](#pin-run-config-resolver-and-pinner) | Run-config resolver and pinner | 28 | 12 |
| [AUD](#aud-audit-chain-and-config-audit) | Audit chain and config audit | 17 | 7 |
| [NTF](#ntf-change-notifier-and-live-layer) | Change notifier and live layer | 13 | 6 |
| [CPA](#cpa-admin-api-authorizer-and-explain) | Admin API, authorizer and explain | 21 | 8 |
| [RTE](#rte-chat-routes-and-routing) | Chat routes and routing | 24 | 6 |
| [ADM](#adm-admission) | Admission | 25 | 9 |
| [JRN](#jrn-journal-execution-store-outbox-and-sweepers) | Journal, execution store, outbox and sweepers | 28 | 9 |
| [PRJ](#prj-projection-live-bus-and-streaming) | Projection, live bus and streaming | 28 | 7 |
| [WRK](#wrk-worker-claimer-and-loop-kernel) | Worker, claimer and loop kernel | 33 | 7 |
| [CHD](#chd-child-runs-and-deep-mode) | Child runs and deep mode | 20 | 4 |
| [CTX](#ctx-context-compiler) | Context compiler | 22 | 5 |
| [MGW](#mgw-model-gateway-and-transports) | Model gateway and transports | 25 | 7 |
| [TGW](#tgw-tool-gateway-effects-and-invoker) | Tool gateway, effects and invoker | 22 | 7 |
| [KNW](#knw-knowledge-read-gate-and-publications) | Knowledge read gate and publications | 18 | 5 |
| [ART](#art-artifacts-and-citations) | Artifacts and citations | 16 | 3 |
| [KIT](#kit-deterministic-test-kit) | Deterministic test kit | 17 | 5 |
| [MCP](#mcp-mcp-hub-and-runners) | MCP hub and runners | 27 | 8 |
| [TSC](#tsc-toolsets-and-credentials) | Toolsets and credentials | 24 | 8 |
| [SKL](#skl-skills) | Skills | 15 | 6 |
| [MEM](#mem-governed-memory) | Governed memory | 15 | 6 |
| [APR](#apr-approvals-interactions-flow-guard-and-unknown-effects) | Approvals, interactions, flow guard and unknown effects | 30 | 10 |
| [BUD](#bud-budget-ledger) | Budget ledger | 15 | 5 |
| [SBX](#sbx-sandbox-broker-and-runcode) | Sandbox broker and run_code | 30 | 8 |
| [EGR](#egr-egress-proxy) | Egress proxy | 25 | 5 |
| [WSP](#wsp-workspace-store-snapshots-and-warm-pool) | Workspace store, snapshots and warm pool | 24 | 5 |
| [ENG](#eng-engines-and-engine-controller) | Engines and engine controller | 30 | 8 |
| [MAC](#mac-model-access-routes-and-accounts) | Model access routes and accounts | 15 | 4 |
| [SCH](#sch-scheduler) | Scheduler | 17 | 5 |
| [ERA](#era-erasure-and-retention-e7-e7b) | Erasure and retention (E7, E7B) | 25 | 6 |
| [LAB](#lab-labs-flags-and-rollout) | Labs, flags and rollout | 16 | 6 |
| [SSN](#ssn-shared-sessions) | Shared sessions | 14 | 3 |
| [LEG](#leg-legacy-query-path-todays-loop) | Legacy query path (today's loop) | 11 | 5 |
| [CON](#con-cross-language-contracts-and-codegen) | Cross-language contracts and codegen | 14 | 5 |
| [OBS](#obs-observability-and-logging) | Observability and logging | 10 | 3 |
| [FEC](#fec-frontend-chat-surfaces) | Frontend chat surfaces | 23 | 8 |
| [FEA](#fea-frontend-workspace-and-admin-surfaces) | Frontend workspace and admin surfaces | 17 | 5 |
| [DEP](#dep-deployment-migrations-and-upgrade) | Deployment, migrations and upgrade | 19 | 4 |

## CAT Control-plane catalog

Target design, owner HL; built by **CPD-2** (in flight on `harness/cpd2-catalog`, not merged at `7e356c8f2`). Today the catalog is spread over `backend/python/app/agents/registry/toolset_registry.py`, `backend/python/app/agents/mcp/registry.py`, `backend/python/harness_engines/allowlist.json` and the built-in skill packs. Cases below are written now against `CatalogStore` in [docs/control-plane/03-interfaces.md](../control-plane/03-interfaces.md) §2.2 and the digest scheme in 02-lld §5.4.

**Behaviours.**
1. B1 One catalog lists every kind (toolset, MCP server, skill, engine, plugin) with provenance, version, risk class and `descriptorSetDigest` (FR-13).
2. B2 Item versions are immutable and digest-addressed; an upgrade is a new version, never an edit (NFR-28, FR-35).
3. B3 Release and built-in rows are shared read-only across orgs; org rows are visible to their org only (NFR-15, NFR-12).
4. B4 One digest scheme in both languages; digests are invariant under key order (02-lld §5.4, R3 D10).
5. B5 The generated `CP/catalog/*.json` equals a fresh dump of the registries (cohesion "catalog matches registries").
6. B6 A remote import (npm, GitHub, MCP discovery) that fails leaves existing installations untouched (NFR-38).

**Levels.** S: the cohesion dump check. U/P: digest canonicalisation properties. K: `CatalogStore` contract over in-memory, `ReleaseCatalogStore` and `MongoCatalogStore`. I: the Mongo adapter on the replica set. C: `GET /api/v1/control-plane/catalog` with router guards.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| CAT-01 | happy | Given release rows for 3 toolsets, 2 MCP templates, 1 skill pack and opencode/Codex; when org A lists the catalog; then every kind appears once per version with provenance `release` and its digest | K | in-memory `CatalogStore` contract | **new** |
| CAT-02 | happy | Insert an org row (custom MCP server) for org A; `list(A)` shows it, `list(B)` does not | K | contract | **new** |
| CAT-03 | edge | Second `insert` of the same `ItemRef` raises `ItemExists`; the stored row is unchanged | K | contract | **new** |
| CAT-04 | failure | Insert with a `descriptorSetDigest` that does not match the recomputed digest raises `DigestMismatch` and writes nothing | K | contract | **new** |
| CAT-05 | security | An attempt to insert a shared (release) row with an `orgId` is refused; org B can never deprecate a shared row | K | contract | **new** |
| CAT-06 | concurrency | `list` pagination is stable while another writer inserts rows (no duplicate, no skipped item across pages) | K/I | contract + Mongo RS | **new** |
| CAT-07 | happy | Canonical JSON digest of a descriptor equals across Python and TypeScript for every vector in `CP/vectors/digests.json` | K | shared vectors | **partial:** `backend/nodejs/apps/tests/modules/agent-harness/contracts/canonical-json.test.ts` (harness canonical JSON only) |
| CAT-08 | edge | Property: digest invariant under key order and whitespace; any change of name, description or input schema changes it | P | hypothesis / fast-check | **new** |
| CAT-09 | failure | A `@Toolset` added in code without regenerating the catalog fails the cohesion dump check naming the toolset | S | cohesion | **new** |
| CAT-10 | happy | The release registry hands each pinned engine its adapter and refuses another digest | U | — | `backend/python/tests/unit/harness_engines/test_release_registry.py::test_rg1_the_release_registry_gives_the_pinned_version_its_adapter_and_refuses_another_digest` |
| CAT-11 | happy | MCP template discovery is idempotent and well-formed; duplicate type ids raise | U | — | `backend/python/tests/unit/agents/mcp/test_registry.py::TestMCPRegistry.test_auto_discover_is_idempotent`, `TestMcpServerDecorator.test_duplicate_type_id_raises` |
| CAT-12 | failure | A catalog import from an unreachable npm source fails with a typed error; org installations and their revisions are unchanged (NFR-38) | C | fake importer | **new** |
| CAT-13 | security | `GET /api/v1/control-plane/catalog` without a session is 401; a member sees only `visible` items with no descriptor secrets | C | in-process Express | **new** |

**Fakes and contract suites.** Planned `backend/nodejs/apps/tests/modules/control-plane/support/catalog-store-contract.ts` (05-migration-plan §6.1) with in-memory and Mongo subclasses; canonical JSON helpers `backend/python/harness_core/contracts/canonical_json.py` and `backend/nodejs/apps/src/modules/agent-harness/contracts/canonical-json.ts`. No catalog port exists yet, so there is nothing for C-2 to require.

**Existing tests.** 4 unit files, 127 tests (inventory), all on today's registries: `test_toolset_registry.py` (86, registry mechanics and sanitisation, mostly coverage-shaped), `agents/mcp/test_registry.py` (23), `harness_engines/test_release_registry.py` (16, release pins and digests), `harness/test_toolset_registry_support.py` (2, registry restore between tests). Nothing tests the catalog as one model across kinds.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| CAT-G1 | `CatalogStore` contract suite over every adapter (CAT-01..06) | 4 | 3 | 12 | K/I | PR unit + PR harness lane (Mongo itest) | `catalog-store-contract.ts` + in-memory/Mongo subclasses | CPD-2 |
| CAT-G2 | One digest scheme, cross-language vectors and key-order property (CAT-07, CAT-08) | 4 | 3 | 12 | K/P | PR unit | `CP/vectors/digests.json` read by a Python and a TS test; hypothesis + fast-check property | CPD-2 |
| CAT-G3 | Cohesion "catalog matches registries" (CAT-09) | 3 | 3 | 9 | S | PR unit (cohesion) | `tests/cohesion/test_cxx_catalog_dump.py` regenerating and diffing `CP/catalog/*.json` | CPD-2 |
| CAT-G4 | Catalog route guards and member visibility (CAT-13) | 3 | 2 | 6 | C | PR unit | route test + C-26 inventory extended to `/control-plane` | CPD-11 |
| CAT-G5 | Remote import failure isolation (CAT-12) | 3 | 2 | 6 | C | PR unit | fake importer raising mid-import | CPD-2, CPD-12 |

## POL Policy engine (dimensions, precedence, narrowing)

Partial, owner HL. Built today for engine profiles and approvals: `backend/nodejs/apps/src/modules/agent-harness/domain/engine-profiles.ts`, `domain/approvals.ts`, `backend/python/harness_core/engines/profiles.py`, `backend/python/harness_core/effects/policy.py`. The generic `PolicyResolver` and `DimensionRegistry` are **CPD-3**; cases POL-12..POL-24 are written against 03-interfaces §4.1 and the invariants in 02-lld §3.4.

**Behaviours.**
1. B1 Layers apply in order deployment → org → team → agent → user → request; each ceiling dimension can only narrow below the layer above (NFR-17, FR-9).
2. B2 A widening is refused at write time and again at admission, naming the first widening layer and dimension (NFR-17, NFR-47).
3. B3 A choice lies inside its ceiling; an unset choice falls back to the ceiling's default.
4. B4 Meet is idempotent, commutative and associative per order kind; the result is ⊑ every ceiling (NFR-47).
5. B5 Python and TypeScript resolve every shared vector identically (R2 F10, D19).
6. B6 An unreadable or invalid layer fails closed for authority-granting dimensions (NFR-31..34).
7. B7 Stricter-only holds on every dimension R2 F5 found open: reasoning effort, deadline vs `maxWallS`, budgets vs profile limits, a loop parent's engine children, loop models vs configured models.

**Levels.** U/P: resolver logic and properties in both languages. K: shared vectors (`contracts/harness/vectors/**`, planned `C/vectors/policy-resolve.json`). C: admin write routes refuse widenings (covered in CPA). X: admission re-validates at pin (covered in PIN).

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| POL-01 | happy | Release → org → agent → request engine-profile layers resolve in order; each narrowing applies | U | — | `backend/python/tests/unit/harness_core/engines/test_profile_resolver.py::test_ep10_release_then_org_then_agent_then_request` |
| POL-02 | happy | Every dimension narrows (one case per dimension) | U | — | `test_profile_narrowing.py::test_ep1_every_dimension_narrows`; Node `engine-profiles.test.ts` "EP-1 narrows" |
| POL-03 | failure | Every widening is refused naming its dimension | U | — | `test_profile_narrowing.py::test_ep2_every_widening_is_refused_naming_its_dimension`; Node "EP-2 refuses" |
| POL-04 | edge | Composing narrowings equals one narrowing; narrowing twice by one layer is a no-op | P | hypothesis / fast-check | `test_profile_narrowing.py::test_ep5_composing_narrowings_equals_one_narrowing`; Node "EP-5" |
| POL-05 | edge | An accepted narrowing is within its base for random layers | P | — | `test_ep4_an_accepted_narrowing_is_within_its_base`; Node "EP-4" |
| POL-06 | happy | Every shared profile vector resolves as the table says in both languages | K | `contracts/harness/vectors` | `test_profile_vectors.py::test_ep7_every_case_resolves_as_the_table_says`; Node `engine-profiles.test.ts` "${vector.id}" |
| POL-07 | failure | An unreadable ceiling refuses and never falls back to defaults; an invalid one refuses | U | — | `test_profile_resolver.py::test_ep12_an_unreadable_ceiling_refuses_and_never_falls_back`, `test_ep12_an_invalid_ceiling_refuses` |
| POL-08 | edge | A ceiling changed after admission reaches only the next run | U | — | `test_profile_resolver.py::test_ep11_a_changed_ceiling_reaches_the_next_resolution_only`; Node `engine-profile-pinning.test.ts` "EP-19" |
| POL-09 | happy | Approval policy: no config allows every class; one switch asks for writes and execution; a later layer only narrows; a tool rule narrows its tool only | U | — | `backend/python/tests/unit/harness_core/effects/test_policy.py::test_ap1..ap4` |
| POL-10 | happy | Approval rule documents: Python accepts exactly what the shared schema accepts; reads every document as Node does | K | approvals vectors | `test_approvals_parity.py::test_ad1_ad2_*`, `test_ad3_*`; Node `approvals-parity.test.ts` |
| POL-11 | failure | An unknown top-level key in the approvals document fails closed once (logged once) | U | — | `test_approvals_parity.py::test_ad3_an_unknown_top_level_key_fails_closed_once` |
| POL-12 | edge | Property over random dimension sets and order kinds: meet idempotent, commutative, associative | P | hypothesis + fast-check | **new** |
| POL-13 | edge | Property: `resolve(layers)[d] ⊑ layer[d]` for every ceiling a layer sets; inserting a layer never widens | P | — | **partial:** `test_profile_narrowing.py` (engine dimensions only) |
| POL-14 | edge | Property: `resolveFrom(foldPrefix(a), b) == resolve(a ++ b)` (precomputed folds equal full resolution) | P | — | **new** |
| POL-15 | failure | The first refusal names the top-most widening layer when two layers widen different dimensions | U | — | **new** |
| POL-16 | security | Reasoning effort above the profile ceiling is refused or clamped (R2 F5; today taken from the client first, `run-spec.ts:414-420`) | U/K | vector | `backend/nodejs/apps/tests/modules/agent-harness/narrow-only.test.ts` NO-1..NO-3 (CPS, merged at `a168c7d5c`) |
| POL-17 | security | `deadlineMs` 24 h with `maxWallS` 1800 resolves to min(deadline, wall) (R2 F5) | U/K | vector | `backend/nodejs/apps/tests/modules/agent-harness/narrow-only.test.ts` NO-4 (CPS, merged at `a168c7d5c`) |
| POL-18 | security | Run budgets above `profile.limits.{maxTokens,maxUsd}` resolve to the profile limit | U/K | vector | `backend/nodejs/apps/tests/modules/agent-harness/narrow-only.test.ts` NO-5 (CPS, merged at `a168c7d5c`); org and agent budgets wait for CP7 |
| POL-19 | security | A loop parent's engine child includes the agent's own `engineProfile` layer (R2 F5, `composition.ts:557-571`) | U | — | `backend/nodejs/apps/tests/modules/agent-harness/child-runs-engine-authority.test.ts` NO-6 (CPS, merged at `a168c7d5c`) |
| POL-20 | security | A loop run of an agent with no models may not pick a model the org has not configured (R2 F5) | U | — | **partial:** QA-2 `contracts/harness/vectors/stricter-only.json` QA2-9a..e and QA2-5 in both languages (a choice outside `models.routes` is refused naming the chooser; with no routes from any layer it is unbounded); admission does not give the org's configured models as `models.routes` yet (QA2-O2), so `spec-pinning.test.ts` "an agent without models runs on the org model the client picked" still holds |
| POL-21 | edge | Enum parity: `ReasoningEffort` values agree between run spec and engine profile (`minimal`, R2 F17) | K | schema | **covered:** QA-7 QA7-42 (`RequestedEffort` named apart from the profile's); QA-2 QA2-8 (every requested effort narrows to a profile effort, `minimal` to the bottom, both languages) |
| POL-22 | happy | Approvals as the `approval-rules` order: strictest-of layers equals today's `LayeredApprovalPolicy` and `_StrictestOf` for every vector (removes R2 F19 duplicate) | K | approvals vectors | `tests/unit/harness/policy/test_policy_properties.py::test_pol22_*` (QA-10) |
| POL-23 | failure | A dimension in `policy-dimensions.json` without a `flags.json` row (operator-only) or vice versa fails cohesion | S | C-16 extended | **new** (CPD-3, CP8) |
| POL-24 | edge | Default values come from one release table; the 16-children, 1 h deadline, `MAX_MEMORY_CHARS` and offering defaults have one definition (R2 F10) | S | cohesion | **new** |

**Fakes and contract suites.** Engine-profile vectors in `contracts/harness/vectors/`, `harness_core/testing/contracts/engine_profiles.py`, `access_policies.py`. Planned `C/vectors/policy-resolve.json` read by `tests/unit/control_plane/` (Python) and `tests/modules/control-plane/` (Node).

**Existing tests.** 10 unit files, 61 tests. Strong on engine profiles (EP-1..EP-23, properties via hypothesis and fast-check in `test_profile_narrowing.py` and `engine-profiles.test.ts`), approvals (AP-1..AP-6, AD-1..AD-3). No generic engine, no property over arbitrary order kinds, no vector for the F5 dimensions.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| POL-G1 | Stricter-only vectors for effort, deadline, budgets, loop-parent engine child, loop models (POL-16..20) | 5 | 4 | 20 | U/K | PR unit | vectors in both languages + Node admission tests asserting refusal codes | **closed by CPS except loop models vs configured models (POL-20)** (`a168c7d5c`) |
| POL-G2 | Generic resolver property suite (POL-12..15) in both languages | 5 | 3 | 15 | P | PR unit (ci profile), nightly 5,000 examples | `tests/unit/control_plane/test_policy_properties.py`, `policy-properties.test.ts` | CPD-3 |
| POL-G3 | One release defaults table (POL-24, R2 F10) | 3 | 4 | 12 | S | PR unit | cohesion scan for duplicated constants with an allow-list that shrinks | CPD-3 |
| POL-G4 | `approval-rules` order replaces `_StrictestOf` with vectors unchanged (POL-22) | 4 | 2 | 8 | K | PR unit | approvals vectors through the generic resolver | CPD-3 — **closed by QA-10** |
| POL-G5 | Dimension ↔ flag matrix cohesion (POL-23) | 3 | 2 | 6 | S | PR unit | C-16 extension | CPD-3, CP8 |
| POL-G6 | Enum parity check (POL-21) | 2 | 3 | 6 | K | PR unit | schema test both languages | CPS |

## CST Control-plane stores and write transaction

Partial, owner HL. Today: five `Config*` classes over deployment-wide `{orgs:{…}}` KV documents (`backend/nodejs/apps/src/modules/agent-harness/application/*-settings.ts`, `config-documents.ts`, `adapters/configuration/encrypted-config-document-store.ts`). Target: `ControlPlaneTransaction` (**CPD-1**), `PolicyStore` (**CPD-4**), `InstallationStore`/`GrantStore` (**CPD-7**), `BindingStore` (**CPD-8**), `ApprovalStore` (**CPD-9**); cases written against 03-interfaces §2.

**Behaviours.**
1. B1 Every write is compare-and-set on an integer revision; a stale revision is `RevisionConflict` and the current revision is unchanged (NFR-27, FR-18).
2. B2 History is append-only, newest first, and survives a rollback write; rollback is itself a new revision (FR-18).
3. B3 One transaction writes the object, its history, the per-org head revision, the audit entry and the outbox row; any failure leaves none of them (NFR-36).
4. B4 Every store method takes `orgId` from the principal; org A never reads or writes org B; one org's corrupt document never degrades another (NFR-12, NFR-13, R2 F8).
5. B5 Batched reads are one round trip per store (`getMany`, `readMany`) (NFR-10).
6. B6 Binding documents never hold a secret (NFR-19).
7. B7 Standalone Mongo uses ordered steps with a repair path and reports degraded health (02-lld §6.4).
8. B8 Migration from the KV documents is a registered D2 migration: dry run, apply, revert, idempotent; dual-write for one release (NFR-29).

**Levels.** K: one contract per store over in-memory and Mongo. I: replica set (transactions) and standalone (ordered steps). N-chaos: KV down during migration. U: today's encrypted config store.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| CST-01 | happy | `ControlPlaneTransaction.run` commits a policy write: head +1, one history row, one `config.changed`, one outbox row | K | transaction contract | **new** |
| CST-02 | failure | `work` throws after writing the object: no object change, no history, head, audit or outbox row | K/I | contract + Mongo RS | **new** |
| CST-03 | failure | `ConfigAuditLog.record` fails: the whole write aborts (NFR-36) | K | fault-injecting audit fake | **new** |
| CST-04 | concurrency | Two concurrent writes in org A produce consecutive head revisions; writes in org A and B do not serialise | I | Mongo RS | **new** |
| CST-05 | concurrency | `PolicyStore.write` with a stale `expectedRevision` is `RevisionConflict`; stored layer unchanged | K | policy-store contract | **new** |
| CST-06 | happy | `write(null)` deletes the layer and records a history row; `history` newest first | K | contract | **new** |
| CST-07 | failure | A schema-invalid stored layer for org A reads as `CP_UNAVAILABLE` for A only; org B resolves normally (NFR-13) | K | contract | **new** |
| CST-08 | security | Every store: org B's id in a read or write for a principal of org A returns nothing / is refused | K | every store contract | **new** |
| CST-09 | edge | `GrantStore.getMany` of 50 subjects is one query (adapter query counter); > 500 entries rejected | K | contract with counting adapter | **new** |
| CST-10 | security | `BindingStore.upsert` refuses a document with any field matching `contracts/harness/vectors/token-patterns.json` | K | token-pattern vector | **new** |
| CST-11 | edge | Standalone Mongo: ordered steps leave a recoverable intermediate state after a crash between steps; `ControlPlaneRepair` completes it; health says degraded | I | standalone mongo:8 | **new** |
| CST-12 | happy | D2 migration of the five KV documents: dry run writes nothing and reports counts; apply copies per org; revert restores reads from KV; second apply is a no-op | I | migration harness (`tests/cohesion/test_c15_data_migrations.py` rules) | **new** (CPD-4) |
| CST-13 | failure | KV unavailable mid-migration: the migration stops, nothing half-copied is served, a rerun completes (05 §6.1) | N-chaos | KV fault | **new** |
| CST-14 | happy | Today: memory policy stored encrypted at its own path; gateway keyring apart from attempt keyring | U | in-memory config store | `backend/nodejs/apps/tests/modules/agent-harness/configuration-stores.test.ts` |
| CST-15 | failure | Today: an unreadable platform settings store is an error, never "off" | U | — | `platform-settings-adapters.test.ts` "an unreadable store is an error, never \"off\"" |
| CST-16 | concurrency | Today: approvals admin save over a newer save is refused (only approvals had client OCC, R2 F8) | C | — | `approvals-admin.http.test.ts` "AW-5" |
| CST-17 | concurrency | Today: engine profile, model access, memory and retention admin saves race; the last writer silently wins (R2 F8) | C | — | **new** (documents current behaviour until CPD-4) |

**Fakes and contract suites.** `InMemoryConfigDocumentStore` (`adapters/in-memory-config-document-store.ts`, R3 M14 says it should move to test support). Planned `tests/modules/control-plane/support/{transaction,policy-store,installation-store,grant-store,binding-store,approval-store}-contract.ts`. `ConfigDocumentStore` has no shared contract suite today.

**Existing tests.** 3 unit files, 16 tests (`configuration-stores.test.ts`, `config.test.ts`, `platform-settings-adapters.test.ts`), plus per-route OCC in `approvals-admin.http.test.ts`. No transaction, history, rollback or per-org isolation suite.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| CST-G1 | `ControlPlaneTransaction` contract: atomicity and audit abort (CST-01..04) | 5 | 3 | 15 | K/I | PR unit + PR harness lane (Mongo RS) | `transaction-contract.ts` | CPD-1 |
| CST-G2 | Per-org isolation and corrupt-document isolation for every store (CST-07, CST-08) | 5 | 3 | 15 | K | PR unit | one shared isolation case in each store contract | CPD-1, CPD-4 |
| CST-G3 | Concurrent saves on the four admin documents without OCC (CST-17, R2 F8) | 4 | 3 | 12 | C | PR unit | race test per route asserting 409 after CPD-4 | CPD-4 |
| CST-G4 | Migration dry-run/apply/revert/idempotence and KV-down fault (CST-12, CST-13) | 4 | 3 | 12 | I/N-chaos | PR harness lane; nightly fault | migration itest + fault case | CPD-4 |
| CST-G5 | Standalone ordered-steps repair (CST-11) | 3 | 3 | 9 | I | nightly (standalone Mongo job) | `mongodb_replset_test.sh` standalone variant + repair itest | CPD-1 |
| CST-G6 | Batched read round-trip counter (CST-09, NFR-10) | 3 | 3 | 9 | K | PR unit | counting adapter in contract | CPD-7 |
| CST-G7 | Binding secret-free schema check (CST-10) | 5 | 1 | 5 | K | PR unit | token-pattern scan in binding contract | CPD-8 |

## PIN Run-config resolver and pinner

Partial, owner HL. Today: `resolveRunSpec` and claim-time pinning in `backend/nodejs/apps/src/modules/agent-harness/domain/run-spec.ts`, `domain/effective-policy.ts`, `backend/python/app/agents/run_definition.py`, and Python's pin adoption (`app/harness/journal_node/client.py`). Target: one `RunConfigPinner.pin(subject)` with `EffectiveSetResolver` and `ResolvedSnapshotCache`, **CPD-5** (shadow then on) and **CPD-6** (pin live policies); cases against 03-interfaces §4.3-4.5.

**Behaviours.**
1. B1 A run's authority is resolved once at admission and pinned; wake, resume, retry and child spawn never re-resolve (FR-29, FR-30, TEST-STRATEGY §2.9).
2. B2 The client can only narrow tools, knowledge and models; it cannot add a tool or model the agent does not grant (FR-5).
3. B3 Same subject and same snapshot give the same digest, across languages and definition order (FR-27).
4. B4 Every admission path uses the same pinner: chat submit, regenerate, child spawn, next-run drain, schedule firing, Slack, MCP, legacy (FR-29, FR-41).
5. B5 The effective set is ⊆ grant ∩ visible ∩ enabled ∩ approved; an unavailable item carries the first failing reason; a `required` item refuses (FR-1..FR-3, NFR-16).
6. B6 The pin binds approved descriptor digests; drift at run time is `capability_lost`, never a silent upgrade (FR-28, R2 F4).
7. B7 Live policies (approvals, flow rule, LLM config, chat-mode policy, skills) come from the pin; the live layer only narrows (R2 F2, NFR-24).
8. B8 A pin of a newer schema is not claimed by an older worker (NFR-26).
9. B9 Warm resolve p95 ≤ 8 ms (quick) / ≤ 10 ms (agent modes); cold ≤ 40 ms; one store round trip warm (NFR-1, NFR-2, NFR-10).
10. B10 Shadow mode logs and counts mismatches, never acts; `on` requires zero mismatches on the corpus and e2e lanes (05-migration-plan §1 rule 6).

**Levels.** U: today's `resolveRunSpec` and spec pinning. K: cross-language golden pin and effective-set vectors. P: ⊆ property, determinism, `pin ⊓ live ⊑ pin`. X: pin adopted by the worker over the real internal API; resume across a config change. N-lat: pinner benchmark. Es: shadow mismatch gate on the e2e lanes.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| PIN-01 | happy | Resolve a valid core spec with the `x.pipeshub` extension; keep only requested tools the agent grants | U | — | `spec-pinning.test.ts` "resolves a valid core spec…", "keeps only requested tools the agent grants" |
| PIN-02 | edge | `tools: []` grants no tools; MCP servers whose tools were all narrowed away are dropped | U | — | `spec-pinning.test.ts` "an empty tools list grants no tools", "drops MCP servers…" |
| PIN-03 | security | The client may choose among granted models, never add one; admission refuses an ungranted model and writes nothing | U | — | `spec-pinning.test.ts` "the client may choose among the granted models…", "P-14" |
| PIN-04 | happy | Spec digest is canonical, independent of definition order, and matches the cross-language golden (Python reads the same digest) | K | golden pin | `spec-pinning.test.ts` "P-4"; `backend/python/tests/unit/harness/test_pinned_run_spec.py::test_python_computes_the_digest_node_pinned` |
| PIN-05 | happy | Resolves exactly once however often the run is claimed; capability set of the first claim is pinned | U | in-memory store | `spec-pinning.test.ts` "P-1", "P-6" |
| PIN-06 | security | A claim never asks the agent authority again | U | — | `spec-pinning.test.ts` "P-9" |
| PIN-07 | edge | A v2 pin reads as v3 on the loop; a pin this Node cannot read is refused, not served | U | — | `spec-pinning.test.ts` "RS3-5", "RS3-8: a pin this Node cannot read is refused" |
| PIN-08 | happy | A v3 loop spec runs exactly as its v2 spec | X | durable parity world | `backend/python/tests/cohesion/test_c9_runspec_v3.py` |
| PIN-09 | edge | Built-in chat in each mode: knowledge and tools follow the mode, never the request | U | — | `spec-pinning.test.ts` "the client can only narrow: tools and knowledge follow the mode" |
| PIN-10 | happy | `effectivePolicy` of an engine run shows grant, routes, budgets and profile from the pin; a loop run has no engine; a blocked call names its rule | U | — | `effective-policy.test.ts` "PV-1", "PV-2", "PV-3" |
| PIN-11 | security | `GET …/effectivePolicy` answers only for the run the caller's scope authorised, never a client-supplied id | U | — | `effective-policy.test.ts` "PV-1: GET run asks for the policy of the run its scope authorized" |
| PIN-12 | happy | Skills are pinned by the content digest of their granted version | U | — | `backend/python/tests/unit/agents/test_run_definition_skills.py::test_each_skill_is_pinned_by_the_content_of_its_granted_version` |
| PIN-13 | security | The run-definition route needs execute scope; another org's agent via a service token is refused; a store failure is an error, not a denial | U | FastAPI test client | `backend/python/tests/unit/agents/test_run_definition.py::TestRunDefinitionRouteScopes`, `TestRunDefinitionRoute.test_sf6_a_service_token_is_refused_another_orgs_agent`, `test_a_store_failure_is_an_error_not_a_denial` |
| PIN-14 | security | MCP pin binds the hub-approved manifest digest; toolset pin binds name + description + schema; a changed schema between admission and claim is `capability_lost` (R2 F4) | U/X | fake hub | `backend/python/tests/unit/harness/assembly/test_descriptor_pins.py` PN-4..PN-11, `backend/nodejs/apps/tests/modules/agent-harness/descriptor-pins.test.ts` PN-3, PN-9, vector `contracts/harness/vectors/descriptor-pins.json` (CPS, merged at `a168c7d5c`); PN-13..PN-15 there and in `backend/nodejs/apps/tests/modules/control-plane/digests.test.ts` (QA-3: the whole schema, effect, sink and MCP hints move the digest) |
| PIN-15 | security | Admin loosens approvals mid-run: the running attempt keeps the pinned (stricter) policy; tightening applies through the live layer (R2 F2, NFR-24) | X | in-memory journal + worker | **new** (CPD-6) |
| PIN-16 | security | Admin repoints `modelKey` while a run is suspended; the resumed run uses the pinned model, not the new one (R2 F2, `assembly/model.py:136-152`) | X | — | **new** (CPD-6) |
| PIN-17 | security | Next-run drain after the agent's grant narrowed is refused with a typed reason or narrowed (R2 F6) | U/K | collaborator-turns contract | `backend/nodejs/apps/tests/modules/agent-harness/next-run-readmission.test.ts` NR-1..NR-6 (CPS, merged at `a168c7d5c`) |
| PIN-18 | edge | Property: effective set ⊆ grant ∩ visible ∩ enabled ∩ approved for random snapshots; same snapshot → same digest | P | `CP/vectors/effective-set.json` | **new** |
| PIN-19 | failure | `required` item missing a credential refuses admission; a non-required one is `unavailable{credential_missing}` and the run proceeds (FR-2, Q3) | U | — | **new** |
| PIN-20 | edge | Child pin ⊑ parent on every ceiling for random parents | P | — | **partial:** `backend/python/tests/unit/harness_core/journal/test_child_grants.py::test_child_grants` (grants table, not every ceiling) |
| PIN-21 | failure | Warm cache, head revision unreadable: entry younger than 30 s used; older fails closed `control_plane_unavailable`; no entry fails closed (NFR-31, NFR-32) | U | `ResolvedSnapshotCache` with fake clock | **new** |
| PIN-22 | concurrency | A write lands between snapshot read and admission commit: the head check inside the admission transaction refuses or re-resolves (02-lld §6.3) | I | Mongo RS | **new** |
| PIN-23 | edge | A revision gap drops the org's cache entries; untouched entries advance | K | snapshot cache contract | **new** |
| PIN-24 | security | Cache keys include `orgId` and revision; two orgs with identical agent ids never share an entry across replicas (NFR-14) | K/I | two Node instances | **new** |
| PIN-25 | happy | Every admission path calls the pinner: a spy on `RunConfigPinner.pin` is hit once each for submit, regenerate, child, drain, schedule, Slack, MCP | C | in-process Express | **new** (CPD-5) |
| PIN-26 | happy | Shadow mode: old and new resolver agree on every replay-corpus run and every e2e row; a planted difference increments `pipeshub_cp_shadow_mismatch_total{dimension}` and changes nothing | X/Es | replay corpus | **new** (CPD-5) |
| PIN-27 | edge | NFR-1/NFR-2 benchmark: 200 items warm, p95 ≤ 8 ms on CI; cold ≤ 40 ms | N-lat | bench | **new** |
| PIN-28 | edge | A worker without `resolved_config` never claims a run pinned with `x.pipeshub.resolved` | U | worker-compat | **new** (CPD-6; pattern in `worker-compat.test.ts` "RU-2g") |

**Fakes and contract suites.** `backend/nodejs/apps/tests/modules/agent-harness/support/golden-run-spec.ts` (golden pin), `harness_core/testing/run_spec.py`, `InMemoryExecutionStore`. Planned: effective-set vectors, `ResolvedSnapshotCache` contract with a `SnapshotSource` fake and manual clock.

**Existing tests.** 7 files, 66 tests: `spec-pinning.test.ts` (32: narrowing, digest, versions, pin-once), `effective-policy.test.ts` (5), `test_run_definition.py` (18), `test_pinned_run_spec.py` (5), C-9. Nothing covers live-read widening, drift binding, shadow mode or pinner latency.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| PIN-G1 | Live policies change a run in flight: loosen approvals, repoint `modelKey`, change chat-mode policy and skills mid-run (PIN-15, PIN-16, R2 F2) | 5 | 4 | 20 | X | PR harness lane | resume-across-config-change tests on the in-memory journal, then on Mongo | CPD-6 (write now as xfail-strict documenting today) |
| PIN-G2 | Pins bind approved descriptors; drift is `capability_lost` (PIN-14, R2 F4) | 5 | 4 | 20 | U/X | PR unit | per-source drift test | **closed by CPS (PN-1..PN-12)** (`a168c7d5c`) |
| PIN-G3 | Next-run drain re-admission (PIN-17, R2 F6) | 5 | 3 | 15 | K | PR unit | collaborator-turns contract extension | **closed by CPS (NR-1..NR-6); CPD-5 moves it into the pinner** (`a168c7d5c`) |
| PIN-G4 | Shadow mismatch gate before `CONTROL_PLANE_PIN=on` (PIN-26) | 5 | 3 | 15 | X/Es | PR replay + nightly e2e | shadow comparator over the replay corpus and covering-array rows | CPD-5 |
| PIN-G5 | Effective-set ⊆ property and determinism (PIN-18) | 5 | 3 | 15 | P | PR unit | hypothesis + fast-check over random snapshots | CPD-7 |
| PIN-G6 | One pinner on every admission path (PIN-25) | 4 | 3 | 12 | C | PR unit | spy test per route | CPD-5 |
| PIN-G7 | Fail-closed and bounded-stale cache (PIN-21, PIN-23) | 4 | 3 | 12 | U/K | PR unit | cache contract with `ManualClock` | CPD-5 |
| PIN-G8 | Cross-org cache isolation over two Node replicas (PIN-24) | 5 | 2 | 10 | I | PR harness lane | two-instance itest | CPD-5 |
| PIN-G9 | Pinner latency benchmark (PIN-27, NFR-1/2) | 3 | 3 | 9 | N-lat | PR (assertion), weekly perf | `bench_harness.py --profile pin` or a mocha bench | CPD-5 |
| PIN-G10 | Required vs optional unavailable items (PIN-19, FR-2) | 3 | 3 | 9 | U | PR unit | admission tests | CPD-7 |
| PIN-G11 | Head check inside the admission transaction (PIN-22) | 4 | 2 | 8 | I | PR harness lane | race itest | CPD-5 |
| PIN-G12 | Claim gating on `resolved_config` (PIN-28) | 4 | 2 | 8 | U | PR unit | worker-compat case | CPD-6 |

## AUD Audit chain and config audit

Partial, owner SEC. Built: per-org hash chain over journal events and gateway refusals (`backend/nodejs/apps/src/modules/agent-harness/application/audited-journal-store.ts`, `audit-anchoring.ts`, `domain/audit.ts`, `adapters/mongo/mongo-audit-chain-store.ts`), grant auditor (`backend/python/harness_core/audit/`, `app/harness/audit/`). Target: `ConfigAuditLog` appending `config.changed` for every control-plane write in the same transaction, and `interaction.granted` chained (**CPD-1**; 03-interfaces §5.3).

**Behaviours.**
1. B1 Audit records are appended in the same transaction as the event or write they describe; an aborted transaction leaves no record (NFR-21, NFR-36).
2. B2 One linear chain per org under concurrent commits; the verifier detects tampering, deletion, reordering and a rewritten tail through anchors.
3. B3 Records hold metadata and digests only: no prompt, tool argument, secret or memory text.
4. B4 Every control-plane write (catalog import, install, config, grant, policy, binding create/rotate/revoke, approval decision, Labs save, model accounts, schedules, MCP manifest) appends `config.changed {scope, kind, objectRef, revisionFrom, revisionTo, changeDigest, actorRef, approvalRef?}` (FR-26, R2 F1).
5. B5 `interaction.granted` (a conversation grant) and `run.spec_pinned` with the resolved-config digest are chained (NFR-22).
6. B6 Gateway refusals reach the run's chain once per reason, capped, ids and codes only (SEC2).
7. B7 The grant auditor flags any executed call outside the run's grant and is clean on granted calls.

**Levels.** U: chain logic, verifier, projection. I: Mongo chain under concurrency (`audit-chain.mongo.itest.ts`). K: `ConfigAuditLog` contract (in-memory, chain adapter). gate-slow: P9 auditor lane on real engines.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| AUD-01 | happy | Audit-relevant events chain per org from genesis | U | in-memory audit | `audit-chain.test.ts` "chains only audit-relevant events, per org, linked from genesis" |
| AUD-02 | failure | An aborted journal transaction leaves no audit record | I | Mongo RS | `audit-chain.mongo.itest.ts` "O-2a: an aborted journal transaction leaves no audit record" |
| AUD-03 | concurrency | Concurrent commits of one org stay one linear chain | I | Mongo RS | `audit-chain.mongo.itest.ts` "O-2a: concurrent commits of one org stay one linear chain" |
| AUD-04 | security | Verifier detects a changed field, a recomputed hash, deletion (middle and tail), reordering; a consistently rewritten tail only through an anchor | U | — | `audit-chain.test.ts` "detects a changed field…", "detects a deleted record…", "detects reordered records", "catches a consistently rewritten tail…" |
| AUD-05 | security | Content never enters an audit record | U | — | `audit-chain.test.ts` "keeps content out of every audit record" |
| AUD-06 | failure | Anchoring: a failed sink keeps the head unanchored and retries; object-lock sink refuses a rewrite | U | fake sinks | `audit-chain.test.ts` "keeps a head unanchored when the sink fails…", "object-lock sink…" |
| AUD-07 | happy | `session.erased` audits who, why and step counts only | U | — | `audit-projection.test.ts` |
| AUD-08 | security | A gateway refusal with the run's token is journaled once; duplicates and past-cap reports append nothing; another run's token appends nothing | U | — | `gateway-refusals.test.ts` "GR-1", "GR-3", "GR-5" |
| AUD-09 | happy | Grant auditor: granted calls clean, ungranted tool/route/unknown run are violations, reported once across passes | U | — | `backend/python/tests/unit/harness_core/audit/test_grant_auditor.py::test_au1..au5` |
| AUD-10 | happy | Auditor on a real engine run flags a planted ungranted call | gate-slow | Docker, real engine | `backend/python/tests/unit/harness/engines/test_p9_audit_lane.py::test_au8_*` |
| AUD-11 | happy | Each existing admin route (engine profile, approvals, model access, memory policy, retention, model accounts, schedules, MCP manifest) and the Labs save appends exactly one `config.changed` with revisions and actor | C | in-memory `ConfigAuditLog` | **new** (CPD-1) |
| AUD-12 | failure | Audit append fails → the admin write fails with 503 and nothing is stored (NFR-36) | C/K | fault-injecting audit log | **new** |
| AUD-13 | security | `config.changed` for a binding rotation carries a secret digest, never the secret; token-pattern scan over the chain finds nothing | K | token-pattern vector | **new** |
| AUD-14 | happy | `interaction.granted` (Allow for this conversation) is chained with the run and interaction ids (R2 F1) | U/I | — | **new** |
| AUD-15 | happy | The verifier accepts a chain mixing journal records and `config.changed` records; tampering with a `config.changed` record is detected | U | — | **new** |
| AUD-16 | edge | `run.spec_pinned` audit record carries the resolved-config digest after CPD-5 | U | — | **partial:** `audit-chain.mongo.itest.ts` "O-2a: a real run chains its admission, claim, pin and cancellation" (no resolved digest yet) |
| AUD-17 | security | Admission `via` (how the caller authenticated) is in the audited `run.admitted` (LOG P1a open item 9) | U | — | **new** |

**Fakes and contract suites.** `adapters/in-memory-audit.ts`, local and object-lock anchor sinks. **No shared contract suite for the audit chain store** (R3 §3); the in-memory and Mongo adapters are tested separately. Planned `tests/modules/control-plane/support/config-audit-contract.ts`.

**Existing tests.** 8 files, 44 tests: `audit-chain.test.ts` (19), `audit-chain.mongo.itest.ts` (4), `gateway-refusals.test.ts` (4), grant auditor (7 + CLI 5), P9 lane (slow). Weak test: `audit-chain.test.ts` "isAuditRelevant answers exactly the types…" in `audit-projection.test.ts` covers an export with no `src` caller (R3 §4 risk 8, `audit-chain.test.ts:212` at review time).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| AUD-G1 | No configuration change is audited (AUD-11, R2 F1): one test per admin route and Labs | 5 | 4 | 20 | C | PR unit | route matrix with an in-memory audit log; behavioural, not source regex | CPD-1 |
| AUD-G2 | Audit failure aborts the write (AUD-12, NFR-36) | 4 | 3 | 12 | C/K | PR unit | fault-injecting `ConfigAuditLog` | CPD-1 |
| AUD-G3 | `interaction.granted` not chained (AUD-14) | 4 | 3 | 12 | U/I | PR unit + Mongo itest | grant then verify chain | CPD-1 |
| AUD-G4 | Shared contract suite for the audit chain store (in-memory vs Mongo) | 3 | 3 | 9 | K | PR unit + PR harness lane | `audit-chain-store-contract.ts` | none |
| AUD-G5 | Mixed-chain verification (AUD-15) and secret-digest only (AUD-13) | 4 | 2 | 8 | U/K | PR unit | verifier cases | CPD-1 |
| AUD-G6 | Admission `via` audited (AUD-17) | 2 | 3 | 6 | U | PR unit | admission audit record test | none |
| AUD-G7 | Replace weak test: delete `isAuditRelevant` export and its coverage-only test (R3 risk 8) | 1 | 5 | 5 | U | PR unit | delete | none |

## NTF Change notifier and live layer

Target design, owner HL; built by **CPD-6** (with LAT1's `AfterCommitPublisher`). Nothing exists yet; cases are written against `ChangeNotifier` (03-interfaces §5.4), `LiveLayerCache` (02-lld §8.3) and NFR-23/24/33/37/48.

**Behaviours.**
1. B1 A committed control-plane write publishes a change event after commit, via the outbox or `AfterCommitPublisher`, on `control-plane-events` through `MessagingFactory` (Kafka or Redis Streams) (NFR-30, NFR-48).
2. B2 Per-org order is preserved; delivery is at least once; handlers are idempotent; a revision gap drops the org's cache entries.
3. B3 A narrowing (disable, revoke, kill switch, approval tightening) reaches every running attempt's live layer within 5 s p99 with the notifier up and within 30 s (TTL backstop) with it down (NFR-23).
4. B4 A widening never reaches a running attempt (NFR-24).
5. B5 Live layer unavailable and older than 30 s: closed for writes (external-effect tools ask or deny), open for reads inside the pin (NFR-33).
6. B6 `pin ⊓ live ⊑ pin` for any live layer.
7. B7 The live-layer check at tool-call time is in process, ≤ 1 ms p95 (NFR-7).

**Levels.** K: notifier contract (in-memory, Kafka, Redis Streams) in both languages. P: `pin ⊓ live ⊑ pin`. U: `LiveLayerCache` with `ManualClock`. X: Node publish → Python worker live layer → tool call refused. N-chaos: broker down. N-lat: in-process check.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| NTF-01 | happy | A policy write commits; one `config.changed` event for the org is published after commit, carrying the new head revision | K | notifier contract (in-memory) | **new** |
| NTF-02 | failure | The write's transaction aborts: no event is published | K | contract | **new** |
| NTF-03 | edge | Three writes in org A arrive at the subscriber in revision order on Kafka and on Redis Streams | K/I | real broker per backend-matrix job | **new** |
| NTF-04 | edge | The same event delivered twice: the handler applies once (idempotent by revision) | K | contract | **new** |
| NTF-05 | edge | Subscriber sees revision 5 after 3: drops org A's cache entries and refetches; org B untouched | U | `LiveLayerCache` | **new** |
| NTF-06 | happy | Admin revokes MCP instance X while a run holds it: the next call to X's tool is refused `capability_lost` within 5 s p99 (NFR-23) | X | harness stack | **new** |
| NTF-07 | failure | Broker down: the same revoke reaches the running attempt by the 30 s TTL poll; admission of a new run sees it immediately via the head check | N-chaos | toxiproxy / container stop | **new** |
| NTF-08 | security | Admin loosens approvals mid-run: running attempt still asks; the next admission does not (NFR-24) | X | — | **new** |
| NTF-09 | security | Live layer older than 30 s and unrefreshable: a write tool asks or is denied, a read inside the pin runs (NFR-33) | U | `ManualClock` | **new** |
| NTF-10 | edge | Property: for random pins and live layers, `pin ⊓ live ⊑ pin` on every dimension | P | hypothesis + fast-check | **new** |
| NTF-11 | edge | Kill switch flipped: new admissions go to legacy at once; runs in flight finish; the live layer marks `kill_switch` without failing them (FLAGS "Rolling back") | X | — | **partial:** `execution-store-contract.ts` "DUR3-F6: with the kill switch on, a queued durable run still ends cleanly" |
| NTF-12 | edge | Tool-call time live check ≤ 1 ms p95 with 500 items (NFR-7) | N-lat | micro-benchmark | **new** |
| NTF-13 | security | Events carry org, kind, object ref and revision only; no policy body, no secret (NFR-19) | K | token-pattern scan | **new** |

**Fakes and contract suites.** Planned `tests/modules/control-plane/support/notifier-contract.ts` and `backend/python/tests/unit/control_plane/test_notifier_contract.py`; Redis Streams and Kafka subclasses join `backend-matrix.yml` with its discovery convention. Reuse `tests/support/fake_kafka.py`, `fake_redis_connection_provider.py`, `harness_core/testing/clock.py`.

**Existing tests.** None (inventory 0 files).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| NTF-G1 | Narrowing propagation bound with and without the notifier (NTF-06, NTF-07, NFR-23) | 5 | 3 | 15 | X/N-chaos | PR harness lane; nightly fault | stack test with broker toggled | CPD-6 |
| NTF-G2 | Widening never reaches a running attempt (NTF-08) and `pin ⊓ live ⊑ pin` property (NTF-10) | 5 | 3 | 15 | X/P | PR unit + PR harness lane | property + resume test | CPD-6 |
| NTF-G3 | Notifier contract over in-memory, Kafka, Redis Streams; order, dedupe, gap rule (NTF-01..05) | 4 | 3 | 12 | K/I | PR unit; backend matrix (nightly) | contract suites both languages | CPD-6 |
| NTF-G4 | Fail-closed-for-writes stale live layer (NTF-09) | 5 | 2 | 10 | U | PR unit | `ManualClock` test | CPD-6 |
| NTF-G5 | Event payload has no secrets (NTF-13) | 4 | 2 | 8 | K | PR unit | scan | CPD-6 |
| NTF-G6 | In-process live check latency (NTF-12) | 2 | 2 | 4 | N-lat | weekly perf | micro-bench | CPD-6 |

## CPA Admin API, authorizer and explain

Partial, owner HL. Built: per-domain admin routes (`backend/nodejs/apps/src/modules/agent-harness/transport/admin-routes.ts`, `approval-routes.ts`, `memory-routes.ts`, `unknown-effect-routes.ts`; Python `backend/python/app/api/routes/mcp_hub.py`), router guards (C-26). Target: `/api/v1/control-plane/*` through one `cpRoute` factory, `Authorizer` vectors, history and rollback, preview and per-run explain, facades over old routes (**CPD-11**, R2 CP9).

**Behaviours.**
1. B1 Every admin route requires the org-admin decision from the authorizer; org and actor come from the token, never the body (NFR-20, CLAUDE.md).
2. B2 An admin acts on their own org only; another org's ids give 404 and nothing is written.
3. B3 Writes validate against the shared schema and refuse a widening naming the dimension; nothing is stored on refusal.
4. B4 Writes use `If-Match` revisions; a stale write is 409 with the current revision; rollback writes a new audited revision (FR-18).
5. B5 Preview/explain returns the effective set and run config with provenance for (agent, user?, request?) without starting a run; per-run explain returns the pin plus live narrowings (FR-10, FR-27, FR-39).
6. B6 Read failures are typed errors, never fabricated or default views (NFR-39).
7. B7 Old routes keep their shapes (additive only) while they are facades (FR-37, NFR-41).
8. B8 Every route is documented in OpenAPI and in the route-guard inventory; internal routes are never on the public listener.
9. B9 Writes p95 ≤ 300 ms including audit; preview p95 ≤ 250 ms (NFR-8).

**Levels.** C: each route in-process with real guards and in-memory stores. S/cohesion: C-26 guard inventory, OpenAPI coverage. K: `CP/vectors/authz.json` role × action × scope. Es: Playwright admin journeys (edit, 409, rollback, explain). N-lat: write and preview latency.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| CPA-01 | happy | Approvals: nothing stored reads empty; an org admin stores the org's sections and the worker's rule reads them | C | in-memory config | `approvals-admin.http.test.ts` "AW-1", "AW-2" |
| CPA-02 | security | Non-admin refused, nothing written; another org's admin reads and writes only its own org | C | — | `approvals-admin.http.test.ts` "${userId}: refused…", "an admin of another org…" |
| CPA-03 | concurrency | A save over another admin's newer save is refused; two orgs saving are both kept | C | — | `approvals-admin.http.test.ts` "AW-5" |
| CPA-04 | failure | Engine profile ceiling that widens the release: 400 naming the dimension, logged once, nothing stored | C | — | `engine-profile-admin.http.test.ts` "a ceiling that widens the release: 400…" |
| CPA-05 | failure | Model access: an unreadable policy store answers 503, never the default | C | — | `model-access.http.test.ts` "OP-3 (Node)" |
| CPA-06 | security | Unknown-effects console: non-admin refused; another org lists nothing and resolving is 404; resolution is audited with ids only | C | — | `unknown-effects.http.test.ts` "NO-4", "NO-9" |
| CPA-07 | security | MCP manifest: non-admins refused; hub off answers 404; a digest that is not the live one is refused | C | FastAPI client | `backend/python/tests/unit/harness/mcp_hub/test_routes.py::test_ar1_non_admins_are_refused_and_the_hub_off_answers_404`, `test_ar2_a_digest_that_is_not_the_live_one_is_refused` |
| CPA-08 | security | Every agent-harness route puts its declared guard first; self-authenticating routes are listed with their token | S | route walk | `backend/nodejs/apps/tests/cohesion/c26-internal-route-guards.test.ts` |
| CPA-09 | security | Python machine routes: every route under the skipped prefix brings its guard; credential checked before the body | S | — | `backend/python/tests/cohesion/test_c26_machine_routes.py::test_rs1_*`, `test_rs3_*` |
| CPA-10 | happy | Every agent-harness route is in `pipeshub-openapi.yaml` with its method | S | — | `backend/nodejs/apps/tests/cohesion/openapi-harness-routes.test.ts` |
| CPA-11 | concurrency | Engine profile, model access, memory policy and retention saves with a stale `If-Match`: 409 with the current revision (R2 F8; only approvals have OCC) | C | — | **new** (CPD-4/CPD-11) |
| CPA-12 | happy | History lists revisions newest first; rollback to revision 2 creates revision N+1 equal to 2's content and one `config.changed` | C | in-memory `PolicyStore` | **new** |
| CPA-13 | happy | Preview for (agent, user) lists each item with state (ready, needs credential, pending approval, not visible) and each dimension with provenance; nothing is admitted | C | in-memory pinner | **new** (FR-10) |
| CPA-14 | security | Preview for another user requires admin; a builder sees only items grantable by them (FR-8) | C | authz vectors | **new** |
| CPA-15 | happy | `GET …/runs/{id}/resolved` returns the pin plus live narrowings applied; another org's run is 404 | C | — | **partial:** `effective-policy.test.ts` "PV-1" (today's `effectivePolicy`) |
| CPA-16 | security | Authorizer matrix: role (member, builder, team admin, org admin, operator) × action × scope from `CP/vectors/authz.json`; team admin outside its delegation refused (FR-21) | K | vectors | **new** (CPD-9/11) |
| CPA-17 | edge | Facades: `/admin/approvals`, `/admin/memory-policy`, `/admin/session-retention` return byte-identical shapes before and after CPD-4 (golden responses) | C | goldens | **new** |
| CPA-18 | security | Approvals `default` and `agents[*]` layers are writable only by the operator path (today: direct KV edit, R2 F16) | C | — | **new** |
| CPA-19 | security | Node toolsets router requires scopes on every route; dead proxy routes return 404 (R2 F16, `toolsets_routes.ts:311-415`) | C | — | **partial:** `backend/nodejs/apps/tests/modules/toolsets/routes/toolsets-route-scopes.test.ts` TS-1, TS-2 (CPS, merged at `a168c7d5c`); dead proxy routes not covered |
| CPA-20 | happy | Playwright: admin edits a policy section, a second tab saves first, the first gets the conflict notice, reloads, saves; then rolls back and the explain panel shows the old value | Es | compose stack | **new** (CPD-11) |
| CPA-21 | edge | Write p95 ≤ 300 ms with audit; preview p95 ≤ 250 ms on the CI stack (NFR-8) | N-lat | bench | **new** |

**Fakes and contract suites.** In-memory config document store, approver directory fakes (`backend/nodejs/apps/tests/modules/enterprise_search/adapters/harness-approver-directory.test.ts` shows the adapter), `support/admission-http.ts` for in-process HTTP. Planned authz vectors and a route inventory test extended to `/api/v1/control-plane`.

**Existing tests.** 9 files, 61 tests: four component suites (approvals 12, engine profile 8, model access 4, unknown effects 8), memory routes (3), MCP manifest routes (10), three route inventories (C-26 Node 5, C-26 Python 8, OpenAPI 3). Weak tests: the Node cohesion files are regex over source (R3 §4 "Cohesion suite"); keep as inventories, add behavioural route tests for ordering claims.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| CPA-G1 | OCC on the four admin documents without it (CPA-11) | 4 | 4 | 16 | C | PR unit | race tests per route | CPD-4 |
| CPA-G2 | Authorizer matrix vectors (CPA-16), including team delegation | 5 | 3 | 15 | K | PR unit | `CP/vectors/authz.json` in Node and Python | CPD-9, CPD-11 |
| CPA-G3 | Approvals `default`/`agents[*]` and toolsets router scope gaps (CPA-18, CPA-19, R2 F16) | 4 | 3 | 12 | C | PR unit | route tests | **partly closed by CPS (toolsets router scopes); approvals `default`/`agents[*]` API open** (`a168c7d5c`) |
| CPA-G4 | Preview/explain correctness and authorization (CPA-13..15) | 4 | 3 | 12 | C | PR unit | in-memory pinner | CPD-5, CPD-11 |
| CPA-G5 | History and rollback as audited revisions (CPA-12) | 3 | 3 | 9 | C | PR unit | policy history test | CPD-4 |
| CPA-G6 | Facade golden responses (CPA-17, NFR-41) | 3 | 3 | 9 | C | PR unit | recorded responses compared | CPD-4, CPD-11 |
| CPA-G7 | Admin Playwright journey in CI (CPA-20) | 3 | 3 | 9 | Es | nightly e2e | `frontend/tests/e2e/workspace/control-plane-policy.spec.ts` | CPD-11 |
| CPA-G8 | Write/preview latency (CPA-21) | 2 | 2 | 4 | N-lat | weekly perf | bench profile | CPD-11 |

## RTE Chat routes and routing

Built, owner HL. Covers the existing chat routes and the durable/legacy decision: `backend/nodejs/apps/src/modules/enterprise_search/controller/es_controller.ts`, `transport/session-controller.ts`, `transport/surface-route.ts`, `domain/rollout.ts`. One-request routing on the existing routes for every client (FR-40) is **LAT1** (in flight) with the routing decision moving into the pin in **CPD-10**.

**Behaviours.**
1. B1 The server, not the client, decides durable vs legacy, project and agent scope (FR-40, R1 F6, F8).
2. B2 Legacy-only modes (`planExecute`/`verification`, Universal Agent Mode, image, project chats) go to today's loop with no wasted round trip (NFR-3).
3. B3 Kill switch, standalone Mongo, model family not allowed and Labs off route to legacy with a typed reason; runs in flight stay reachable (FLAGS `durable_chat`).
4. B4 `conversation_created` is the first frame on both paths; terminal `RUN_FINISHED` carries the result, a child's does not.
5. B5 Regenerate is admitted like a send and governed by the same pin (FR-41, R1 F2; UX3 made it durable).
6. B6 API/PAT, Slack and MCP clients on the existing routes get the same routing as the web UI; surfaces get their audience (no tool arguments to Slack/MCP).
7. B7 A second message while a run is active joins it (steering) under the same authorization as a new turn.
8. B8 Disconnect mid-answer does not stop a durable run; legacy keeps what the user saw.
9. B9 First frame p95 < 300 ms (SLO #3); durable TTFT overhead ≤ legacy + 75 ms (SLO #2).

**Levels.** U: controller and rollout decision. C: surface route, steering, stream over HTTP in process. K: surface parity and MCP parser contracts. I: regenerate on Mongo RS. Es: browser and MCP client. N-lat: `bench_harness.py`.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| RTE-01 | happy | Rollout admits each durable chat mode; keeps legacy modes on legacy without resolving the model; matches the cross-language mode vector | U | — | `harness-rollout.test.ts` "admits ${chatMode}", "keeps ${chatMode} on legacy…", "matches the cross-language vector…" |
| RTE-02 | edge | Never admits without durable infrastructure; missing flags read as off | U | — | `harness-rollout.test.ts` "never admits without durable infrastructure", "treats missing flags as off…" |
| RTE-03 | happy | Kill switch: org availability reported for the UI and in-flight runs stay reachable | U | — | `harness-rollout.test.ts` "reports org availability for the UI and keeps in-flight runs reachable under the kill switch" |
| RTE-04 | security | A project conversation goes to today's loop whatever the browser sent, even one that once ran durably (R1 F6) | U | — | `ux3-admission-placement.test.ts` "PS-1" (both cases) |
| RTE-05 | edge | No worker seen: a new chat goes to legacy at once; a durable session stays durable (turns together) | U | — | `ux3-admission-placement.test.ts` "QD-2", "QD-3" |
| RTE-06 | security | Session controller derives authority from the authenticated identity and route scope; incomplete identity fails closed | U | — | `session-controller.test.ts` "derives all authority…", "fails closed when authenticated identity is incomplete" |
| RTE-07 | edge | Rollout declines: the controller tells the client to use the legacy path (today's two-request flow) | U | — | `session-controller.test.ts` "tells the client to use the legacy path when the rollout declines" |
| RTE-08 | happy | MCP surface: rollout on, the turn is admitted and streamed in the same response; the MCP parser reads the answer | C | in-process Express | `surface-route.http.test.ts` "SF-1: rollout on…" |
| RTE-09 | security | Surface: org and actor come from the credential; a body naming them stays on legacy; Slack service account runs as the hydrated identity | C | — | `surface-route.http.test.ts` "SF-5" (both) |
| RTE-10 | security | Slack and MCP never receive tool arguments, results, narration they did not get on legacy | K | goldens | `backend/nodejs/apps/tests/contracts/surface-parity.test.ts` "SF-4", "SL-2"; `integration-tests/harness/fault/test_surface_leaks.py` |
| RTE-11 | happy | Steering: a second message on the agent route is 202 with the running run; a retry replays; with none running, `steer: true` is 409 `RUN_NOT_ACTIVE` | C | — | `steering.http.test.ts` (first three cases) |
| RTE-12 | security | Steering has the same authorization as a new turn: revoked agent access 403, another user 404, attachment the caller cannot read 403 | C | — | `steering.http.test.ts` "the same authorization as a new turn…", "C-21b" |
| RTE-13 | happy | Regenerate on a durable conversation asks the last turn again from what is stored; the old answer is removed from history | I | Mongo RS | `ux3-regenerate.mongo.itest.ts` "RG-3" |
| RTE-14 | failure | Regenerate of a non-last answer, another session's answer, or while a run is active is refused; a replayed regenerate removes nothing more | I | — | `ux3-regenerate.mongo.itest.ts` "RG-4", "RG-5", "RG-6" |
| RTE-15 | happy | Legacy streaming: answer streams, saved on finish; sub-agent `RUN_FINISHED` passes through; tab close keeps what the user saw | U | chat test harness | `es_controller.streaming.test.ts` (first, seventh, third cases) |
| RTE-16 | failure | Legacy: AI service breaks mid-answer → interrupted; DB unreachable at end → plain message and stream closed | U | — | `es_controller.streaming.test.ts` "tells the user the answer was interrupted…", "still closes the stream…" |
| RTE-17 | security | Legacy non-streaming: another user's conversation or an unknown id is 404 and nothing is written | U | — | `es_controller.non-streaming.test.ts` "${flow.name}: another user's conversation is a 404…" |
| RTE-18 | happy | The published `@pipeshub-ai/mcp` parser ends only on a `RUN_FINISHED` with a result | K | pinned package | `backend/nodejs/apps/tests/contracts/mcp-package-parser.test.ts`; `integration-tests/mcp_package/integration_test_mcp_package.py` (e2e-client) |
| RTE-19 | happy | An API/PAT client on `POST /api/v1/agents/:key/conversations/stream` in quick mode gets a durable run in one request; in planExecute a legacy run in one request with no prior admission attempt (FR-40, NFR-3) | C | — | `backend/nodejs/apps/tests/modules/agent-harness/qa8-routing-vectors.http.test.ts` (QA8-8: every client kind, every HTTP vector); `lat1-chat-turn.http.test.ts` OR-2..OR-4 |
| RTE-20 | happy | Web UI: one request per turn; no `POST …/runs` then `GET …/stream` pair for new turns; reconnect still uses `GET …/runs/:id/stream` | Es | Playwright | **new** (LAT1) |
| RTE-21 | security | Routing vectors: chat mode × project × kill switch × model family × Labs give the documented path and reason in Node and in the resolved config (CPD-10) | K | `contracts/harness/vectors/routing.json` | `backend/nodejs/apps/tests/modules/agent-harness/qa8-routing-vectors.test.ts` (every vector and 200 random inputs through admission, per adapter); `backend/python/tests/unit/harness/test_qa8_routing_vectors.py`; the e2e oracle reads the same table (QA-8). CPD-10's adapter: one more `routingVectorSuite` call |
| RTE-22 | security | Legacy path receives `X-PipesHub-Resolved` (compact JWS); unknown `kid`, expired or tampered header is refused by the query service | C | Ed25519 keyring fake | **new** (CPD-10) |
| RTE-23 | edge | First frame p95 < 300 ms and TTFT overhead within SLO #2 for quick, auto, plain on both routes | N-lat | `integration-tests/perf/bench_harness.py` | **partial:** `--profile short-turn` covers one profile (SLO.md "Not measurable yet") |
| RTE-24 | edge | Kill switch flipped while the browser has cached effective flags: the next turn still routes legacy server-side and the client refetches (R1 F26) | Es | — | **new** |

**Fakes and contract suites.** `backend/nodejs/apps/tests/modules/enterprise_search/controller/chat-test-harness.ts`, `support/admission-http.ts`, `support/rollout.ts`, SSE goldens under `backend/nodejs/apps/tests/contracts/golden`. No shared contract for `RoutingDecider` yet (LAT1 seam, 05-migration-plan §5).

**Existing tests.** 17 files, 168 tests (unit 126, component 21, contract 9, integration 5, MCP client 7). Strong on rollout decisions, surfaces and legacy controller failure paths. Missing the one-request flow, routing vectors across all dimensions, and any CI browser run of durable chat.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| RTE-G1 | Routing vectors over mode × project × kill switch × family × Labs × client kind (RTE-21) | 4 | 4 | 16 | K | PR unit | vector file read by Node and by the e2e generator | LAT1, CPD-10 |
| RTE-G2 | Durable chat browser specs run in CI, not only `deployment/e2e/run.sh` | 4 | 4 | 16 | Es | nightly e2e (compose) | a `harness` Playwright project in a workflow | none |
| RTE-G3 | One-request routing for API/PAT clients and the web UI (RTE-19, RTE-20) | 4 | 3 | 12 | C/Es | PR unit; nightly e2e | component test per route; Playwright network assertion | LAT1 |
| RTE-G4 | `X-PipesHub-Resolved` signature checks (RTE-22) | 5 | 2 | 10 | C | PR unit | query-service route test | CPD-10 |
| RTE-G5 | Latency profiles per mode (RTE-23, SLO #2/#3) | 3 | 3 | 9 | N-lat | weekly perf | `bench_harness.py` profiles quick/auto/plain | none |
| RTE-G6 | Stale cached effective flags after a kill-switch flip (RTE-24, R1 F26) | 2 | 3 | 6 | Es | nightly e2e | Playwright with a Labs flip | none |

## ADM Admission

Built, owner HL. `backend/nodejs/apps/src/modules/agent-harness/application/run-admission-service.ts` (decide), `adapters/mongo/mongo-run-admission-store.ts` (one transaction), `domain/admission-quotas.ts`, `application/next-run-service.ts`, `adapters/http/inline-claim-hint.ts`. CPD-5 replaces the eight serial resolvers with one `RunConfigPinner` call and splits the admission pipeline (R2 F14, CP11).

**Behaviours.**
1. B1 Admission is atomic: run, session active key, admission receipt, `run.admitted`/`run_queued` events and exactly one outbox `run_ready` row in one transaction, or nothing (TEST-STRATEGY §5.1, v1 Gate 3).
2. B2 Idempotent: an identical retry replays the receipt; a changed payload under the same key conflicts; the granted definition is not part of the idempotency hash.
3. B3 At most one active root run per session; a second send steers or gets `409 RUN_ACTIVE`.
4. B4 Identity, org and actor come from the credential; agent access is checked with the caller's own credential and fails closed (503) when the authority cannot answer.
5. B5 Model access, engine profile ceiling, memory pin, roles, labels and worker-feature requirements are decided before the transaction and pinned on the run; an unreadable policy refuses, never defaults.
6. B6 Quotas: per-user rate and active-run limits answer 429 with `Retry-After` and admit nothing (R1 F35 for service identities).
7. B7 The inline claim hint is fire-and-forget, ids only, worker credential; a lost hint is recovered by claim-next polling within the claim deadline (90 s for interactive, UX3).
8. B8 Next runs (collaborator messages queued behind a run) are drained through the same checks as a new admission (R2 F6, CPS).
9. B9 Admission p95 < 150 ms (SLO #4); control-plane share within NFR-1/2.

**Levels.** U: decision service with in-memory stores. K: execution-store and memory-admission contracts (in-memory + Mongo). C: admission HTTP (quotas, agent access). I: Mongo RS transaction atomicity and concurrency. X: hint → claim. N-lat: `claim-admission.mongo.bench.ts`, `bench_harness.py`.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| ADM-01 | happy | Server-owned run and dispatch ids built around a canonical payload; a retry with changed input changes the payload hash | U | in-memory | `run-admission-service.test.ts` "builds server-owned run and dispatch IDs…", "changes the payload hash…" |
| ADM-02 | happy | Atomically admits once, replays the receipt, rejects conflicts; built-in chat session created atomically | I | Mongo RS | `backend/nodejs/apps/tests/integration/agent-harness/mongo-admission.integration.test.ts` |
| ADM-03 | edge | One active root run per tenant session; another actor's session is not admitted into | K | execution-store contract | `support/execution-store-contract.ts` "allows one active root run per tenant session", "does not admit into another actor session" |
| ADM-04 | happy | Admission writes exactly one outbox event keyed by run; an aborted admission leaves no outbox row or run | I | Mongo RS | `execution-store.mongo.itest.ts` "admission writes exactly one outbox event…", "an aborted admission transaction leaves no outbox event or run" |
| ADM-05 | security | Agent the actor cannot use is rejected, nothing written; access unverifiable fails closed | U | — | `run-admission-service.test.ts` "rejects an agent the actor cannot use…", "fails closed when access cannot be verified" |
| ADM-06 | security | An agent:execute-only PAT is admitted with its own credential; a read-only token refused before asking; org/user from the token, a body naming them refused; authority down → 503 | C | `support/admission-http.ts` | `agent-access.http.test.ts` (all five) |
| ADM-07 | security | Python's run-definition answer: denied statuses deny, server errors cannot decide, a definition for another agent is refused | U | — | `backend/nodejs/apps/tests/modules/enterprise_search/adapters/harness-agent-access.test.ts` |
| ADM-08 | failure | Quota exceeded: 429 with `Retry-After` and a stable code, nothing admitted; the quota that refused is named | C/U | — | `admission-quotas.http.test.ts`; `admission-quotas.test.ts` "names the quota that refused…" |
| ADM-09 | edge | Model access: org default pins the concrete configured model; the actor's own key pinned and bound into the routes digest; another user's key `account_not_owned` 403; an unreadable org policy 503 | U | — | `model-access-admission.test.ts` "AD-1", "AD-2", "AD-3", "AD-6" |
| ADM-10 | security | A loop run naming a model account is refused `account_requires_engine` | U | — | `model-access-admission.test.ts` "AD-7" |
| ADM-11 | happy | Memory: no policy pins nothing; two users in a shared conversation never get each other's memories; a memory from an unreadable record is not pinned | K | memory-admission contract | `support/memory-admission-contract.ts` "AD-1", "AD-3", "AD-4" |
| ADM-12 | edge | Worker features: an engine run needs `executor_engine`; deep needs `deep_mode`; a Node never serves a run pinned with a newer spec schema | U | — | `worker-compat.test.ts` "CC-4", "NK-5", "RU-2g" |
| ADM-13 | edge | Capability probe fails closed: `journal.durable=false` refuses admission and pinning | U | — | `capabilities.test.ts` "probes journal.durable=false, refuses admission and refuses to pin" |
| ADM-14 | failure | Inline hint never rejects: refused, failed or unprovisioned hint is only logged; the run is still claimed by polling | U/X | — | `inline-claim-hint.test.ts` "never rejects…"; `degraded-modes.test.ts` "a run whose wake hint was never delivered is claimed by claim-next polling" |
| ADM-15 | edge | Replays an admission made before the policy changed (no re-decision on replay) | U | — | `run-admission-service.test.ts` "replays an admission made before the policy changed" |
| ADM-16 | concurrency | 50 concurrent identical admissions → one run, 49 replays; 50 distinct admissions in one session → one admitted, 49 `RUN_ACTIVE` or steer | I | Mongo RS | `qa21-admission-races.mongo.itest.ts` QA21-1, QA21-2 (QA-21) |
| ADM-17 | failure | Failpoint after each of the ~18 transaction operations: nothing is visible, the retry admits once (v1 Gate 3) | I | failpoints (Node) | `qa21-admission-races.mongo.itest.ts` QA21-9 (QA-21) |
| ADM-18 | failure | Blob of imported content written inside the retried transaction: an aborted attempt leaves no orphan blob or records it for cleanup (R3 M8, not fixed by FX) | I | Mongo RS | `qa21-admission-races.mongo.itest.ts` QA21-10 (QA-21; M8 fixed) |
| ADM-19 | security | Next-run drain re-runs agent authorization, engine ceiling, model access and memory; a revoked agent refuses the queued turn `agent_forbidden` (R2 F6) | K | collaborator-turns contract | `backend/nodejs/apps/tests/modules/agent-harness/next-run-readmission.test.ts` NR-1..NR-6 (CPS, merged at `a168c7d5c`) |
| ADM-20 | edge | Kill switch on: a queued next run is refused `kill_switch`, never admitted durably (DUR3 fix) | K | — | **partial:** `support/execution-store-contract.ts` "DUR3-F6" (cancel path), `collaborator-turns.test.ts` |
| ADM-21 | edge | Service identity (MCP/Slack bot) behind one PAT: quota keyed per end user or a separate service quota; 31 turns in a minute from distinct Slack users are admitted (R1 F35) | U/C | — | `qa21-admission-races.mongo.itest.ts` QA21-12 (QA-21: per person) |
| ADM-22 | edge | Independent admission reads (rollout, agent access, engine profile, model access, roles, labels, memory) run concurrently; total decide time ≈ max, not sum (R1 §2.3 item 7) | U | fakes with `ManualClock` delays | **new** (LAT1) |
| ADM-23 | edge | Admission p95 < 150 ms over HTTP on the CI stack at N=16 (SLO #4) | N-lat | `bench_harness.py` | **partial:** `claim-admission.mongo.bench.ts` (store-level only, no gate) |
| ADM-24 | security | Admission records how the caller authenticated (`via`) from the token, never the body | C | — | `session-routes.test.ts` "VA-1/VA-2"; `session-controller.test.ts` "records how the caller authenticated" |
| ADM-25 | edge | Schedule firing, child spawn and regenerate go through the same decision code (one pinner after CPD-5) | C | spy | **partial:** `qa8-routing-vectors.test.ts` QA8-9 (schedule firing, regenerate; send and the surfaces in QA8-8); child spawn and next-run drain do not ask the decider (QA8-O2, CPD-5) |

**Fakes and contract suites.** `InMemoryExecutionStore` and `support/execution-store-contract.ts` (in-memory and Mongo), `support/memory-admission-contract.ts`, `support/collaborator-turns-contract.ts`, `support/admission-http.ts`, `support/agent-definition.ts`, `support/model-access.ts`, `support/rollout.ts`. The in-memory store diverges from Mongo on TOCTOU and history import (R3 §3); FX made in-memory writes serialize. Note R3 §4 risk 9: the execution-store contract is one 4,151-line file.

**Existing tests.** 16 files, 121 tests (unit 92, contract 17, component 6, integration 4, perf 2). Good coverage of decisions and refusal codes; thin on concurrency at admission and on transaction fault points.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| ADM-G1 | Next-run drain re-admission (ADM-19, R2 F6) | 5 | 4 | 20 | K | PR unit + PR harness lane | contract case (in-memory + Mongo) | **closed by CPS (NR-1..NR-6)** (`a168c7d5c`) |
| ADM-G2 | Concurrent identical/distinct admissions (ADM-16) | 4 | 3 | 12 | I | PR harness lane | 50-way race itest | none |
| ADM-G3 | Orphan blobs from a retried admission transaction (ADM-18, R3 M8) | 3 | 4 | 12 | I | PR harness lane | storage write counter vs committed refs | fix M8 |
| ADM-G4 | Transaction failpoints across the admission transaction (ADM-17) | 5 | 2 | 10 | I | PR harness lane | Node failpoints armed per step | none |
| ADM-G5 | Admission SLO #4 gate (ADM-23) | 3 | 3 | 9 | N-lat | weekly perf | `bench_harness.py --profile admission` | none |
| ADM-G6 | Concurrent decide reads (ADM-22) | 3 | 3 | 9 | U | PR unit | timing with fake delays | LAT1 |
| ADM-G7 | Service-identity quota (ADM-21, R1 F35) | 3 | 3 | 9 | U/C | PR unit | quota test | product decision |
| ADM-G8 | One decision path for schedule, child, regenerate (ADM-25) | 4 | 2 | 8 | C | PR unit | spy test | CPD-5 |
| ADM-G9 | Replace weak test: split the 4,151-line execution-store contract by concern (admit, commit, control, children) (R3 risk 9) | 2 | 4 | 8 | K | PR unit | refactor, same cases | none |

## JRN Journal, execution store, outbox and sweepers

Built, owner HL. `backend/nodejs/apps/src/modules/agent-harness/application/execution-service.ts`, `adapters/mongo/mongo-execution-store.ts`, `mongo-journal-store.ts`, `mongo-dispatch-outbox.ts`, `application/sealed-journal-store.ts`, `run-sweeper.ts`, `transport/internal-*.ts`; Python client `backend/python/app/harness/journal_node/client.py`.

**Behaviours.**
1. B1 `seq` is gap-free and ordered per stream under concurrent appends; an aborted transaction advances nothing.
2. B2 Fencing: a commit under a stale fence is refused; a lapsed lease is re-claimable and fences the old owner; leases and deadlines run on DB time.
3. B3 Writers are typed: workers cannot write Node-only types (principal, grant, policy, admission, interaction resolution, session authority) or widen visibility (A-13).
4. B4 Payloads ≤ 256 KB inline, larger claim-checked; corrupt payloads are detected on read; payload store down yields the run with backoff, never `journal_payload_missing`.
5. B5 Event data is sealed (AES-GCM, AAD bound to the envelope); shredding an org's keys makes it unreadable and leaves others readable.
6. B6 The outbox writes one dispatch per transition; it never dead-letters; relay works over Kafka and Redis Streams.
7. B7 Attempt tokens: EdDSA, bound to run and attempt, lease-bounded lifetime, rotation with overlap; the worker credential is valid only for claim (and the grant auditor).
8. B8 The sweeper (leader on DB time) moves every non-terminal state past its deadline to its documented next state, settles memories a crash missed, drains next runs, and holds off after a control-plane outage.
9. B9 A duplicate or replayed commit ends nothing twice (R3 M1, FX-4).
10. B10 Commit latency p99 ≤ 100 ms (1-node RS) / ≤ 250 ms (3-node) at N=16 (SLO #1); commits per tool turn ≤ 2 + 2 × tools (SLO #9).

**Levels.** K: journal-store and execution-store contracts (in-memory + Mongo). U: authority, sealing, sweeper with fake timers, Python client. I: Mongo RS itests including 3-member failover (`journal-failover.rs3.itest.ts`). P: fencing linearizability (Wing-Gong checker). X/N-chaos: crash points (`integration-tests/harness/fault/test_crash_points.py`), upgrades. N-lat: commit latency.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| JRN-01 | happy | Contiguous `seq` across batches; `lastSeq` reported; child streams independent | K | journal-store contract | `support/journal-store-contract.ts` "allocates contiguous seq across batches…", "keeps child streams independent…" |
| JRN-02 | concurrency | Concurrent transactions on one stream stay gap-free and ordered; aborted ones leave no events and no counter advance | I | Mongo RS | `journal-store.mongo.itest.ts` "J-1", "an aborted transaction leaves no events…" |
| JRN-03 | security | Worker refused a Node-only type or a visibility widening, nothing written; unknown type and schema-invalid data refused; `__proto__` key refused | K | contract | `support/journal-store-contract.ts` "refuses a node-only type from a worker…", "A-13", "refuses a reserved __proto__ key…" |
| JRN-04 | security | Another org's stream is never read | K | contract | `support/journal-store-contract.ts` "does not read another org's stream" |
| JRN-05 | edge | ≤ 256 KB inline, larger claim-checked; a corrupt payload detected on read | K | contract | `support/journal-store-contract.ts` "keeps payloads up to 256 KB inline…", "detects a corrupt payload on read" |
| JRN-06 | concurrency | Fencing: expired worker fenced; later commit rejected; renewal bounded by the absolute deadline | K | execution-store contract | `support/execution-store-contract.ts` "fences an expired worker…", "bounds lease renewal…" |
| JRN-07 | concurrency | Linearizability of claim/renew/commit for two workers, sequential and concurrent, in memory and on Mongo | P/I | Wing-Gong checker | `fencing-linearizability.test.ts`; `fencing-linearizability.mongo.itest.ts` |
| JRN-08 | happy | Leases and deadlines anchored on DB time; a live lease cannot be stolen; a lapsed one is re-claimable; sweeper leader on DB time | I | Mongo RS | `db-time-leases.mongo.itest.ts` (all five) |
| JRN-09 | security | Sealing: no plaintext data in stored events; AAD binds content to its event (moved content refused as corrupt); flipped byte refused; shred makes the org unreadable within the cache TTL | U/I | data-protection fakes | `data-protection.test.ts` "stores no plaintext…", "refuses content moved to another event (AAD)…"; `data-protection.mongo.itest.ts` "O-1d" |
| JRN-10 | security | Attempt tokens: alg none, HS256, unknown kid, wrong audience, tampered, expired refused; rotation with overlap; retired key pruned | U | — | `authority.test.ts` "A-7" cases, "A-4" |
| JRN-11 | security | Internal routes: worker credential alone refused after claim; token reused for another run refused; org and worker from the token, never the body; old attempt token refused after re-claim | C | in-process Express | `internal-authority.http.test.ts` "A-8", "A-2", "A-9", "A-3" |
| JRN-12 | failure | Payload store down: commit journals nothing, run yields with backoff, worker told to stop; never `journal_payload_missing` | U | — | `degraded-modes.test.ts` (first four) |
| JRN-13 | failure | KV down: authority documents served stale up to the bound, then fail closed; worker credential accepted within the bound only | U | — | `degraded-modes.test.ts` "serves the last good copy for a bounded time…", "keeps accepting the worker credential while KV is down…" |
| JRN-14 | happy | Outbox: control and worker commits dispatch through the same outbox; a failing publish never dead-letters, the run stays claimable after the broker recovers | I | Mongo RS | `execution-store.mongo.itest.ts` "never dead-letters a failing publish…", "control and worker commits dispatch through the same outbox" |
| JRN-15 | happy | Outbox relay delivers `run_ready` over Kafka and over Redis Streams with ids-only payload | I | backend matrix | **partial:** `backend/nodejs/apps/tests/libs/services/outbox/outbox.dispatcher.test.ts` (mocked broker); `harness-fault.yml` runs the crash suite per broker |
| JRN-16 | failure | 3-member RS failover: no acknowledged commit lost, each applied exactly once | I | 3-node RS | `journal-failover.rs3.itest.ts` |
| JRN-17 | failure | Control-plane outage 45 s: the returning sweeper expires what lapsed on its first tick; explicit holdoff survives a leader change | I/X | — | `sweeper-holdoff.mongo.itest.ts`; `integration-tests/harness/fault/test_upgrades.py::test_c13_recovery_after_a_full_control_plane_outage` |
| JRN-18 | edge | Sweeper: every non-terminal state (`queued`, `running`, `suspended`, `recovering`, `needs_reconciliation`, `cancelling`) past its deadline reaches its documented next state; a dead-lettered dispatch releases `activeSessionKey` (REPORT E5) | K | contract + fake timers | `support/execution-store-contract.ts` "QA13-8 (JRN-18): every non-terminal state at its absolute deadline" (in-memory and Mongo, QA-13); the earlier cases stay |
| JRN-19 | concurrency | Two identical terminal commits racing: the run ends once, `turnEnded` counts once, agent memory kept (R3 M1) | K | contract | `support/memory-admission-contract.ts` "FX4-1" |
| JRN-20 | failure | Crash at every commit-rule crash point (worker SIGKILL): run resumes on another worker, no duplicate effect, no re-sample of a committed turn | X | fault stack, failpoints | `integration-tests/harness/fault/test_crash_points.py::test_crash_point` (parametrized, nightly per broker) |
| JRN-21 | failure | Python client: a non-JSON 2xx or invalid pin in the claim answer is a `JournalError`, the slot is released and polling continues (R3 B1, FX-1) | U | fake journal | `backend/python/tests/unit/harness/test_node_journal_client.py::test_fx1_1_a_claim_answer_this_worker_cannot_read_is_a_journal_error_and_keeps_no_token` |
| JRN-22 | edge | Journal history checks after every L3/L4 test: gap-free seq, monotonic commitSeq, one `run.spec_pinned` first, each outcome has an intent, nothing after terminal except retention markers (TEST-STRATEGY §2.14) | X | shared `afterEach` | `harness_core.testing.history` + `tests/support/journal_history.py` (fixture in the harness packages), Node `tests/support/harness/journal-history.ts` + root hook `tests/helpers/journal-history-hooks.ts` (QA-13); H5 also accepts `tool.denied` and a `cancelled` outcome |
| JRN-23 | edge | Model-based fast-check `fc.commands([Admit, Claim, Renew, Commit, StaleCommit, Cancel, Expire, Sweep, Relay])` against in-memory with a pure model; nightly differential against Mongo | P | fast-check | `execution-store.model.test.ts` (in-memory) and `execution-store.model.mongo.itest.ts` (step-by-step differential, nightly) over `support/run-machine.ts` (QA-13) |
| JRN-24 | edge | Commit latency p99 per commit kind at N=16 meets SLO #1; commits per tool turn ≤ 2 + 2 × tools (SLO #9) | N-lat | `bench_harness.py --container` | **partial:** `harness_replay/test_capacity.py` (projected); measured histogram not built |
| JRN-25 | edge | Migrations: up builds exactly the declared index set once; down drops it; a base-release index is found and not rebuilt | I | Mongo RS | `harness-migrations.mongo.itest.ts` "MG-1".."MG-7"; C-15 |
| JRN-26 | failure | Standalone or PSA Mongo is reported not durable; durable admission refused | U/I | — | `journal-startup.test.ts` "reports a standalone server as not durable", "reports a set with an arbiter (PSA)…"; `.github/workflows/agent-harness-infrastructure.yml` `mongodb-replica-set-default` |
| JRN-27 | security | `seq` is in the AAD, so a reordered event fails to open (LOG P1a open item 4) | U | — | **new** |
| JRN-28 | failure | Python driver: a transient `JournalUnavailable` during a fold read yields and retries, never `kernel_error` (LOG P1a open item 7) | U | in-memory journal with injected unavailability | **partial:** `backend/python/tests/unit/harness_core/journal/test_memory_adapters.py::test_injected_unavailability_and_stale_reads` |
| JRN-29 | edge | A commit of exactly `journal.maxCommitEvents` events is accepted, with and without a terminal transition; one event more is a permanent refusal (`VALIDATION_ERROR` / `CommitRejected`) that writes nothing (PERF-A, QA22-F1) | K | journal contract suite on the in-memory journal and Node's server | `backend/python/harness_core/testing/contracts/journal.py::test_perfa_2_*`; `backend/nodejs/apps/tests/modules/agent-harness/perfa-commit-limit.test.ts` |
| JRN-30 | happy | The capacity model projects a read batch as the commits the kernel makes: past `journal.maxCommitEvents` events a batch is consecutive `outcomes` commits, a read never cut, SLO #9's bound held (PERF-A) | U | — | `backend/python/tests/harness_replay/test_capacity.py::test_perfa_12_*` |

**Fakes and contract suites.** `support/journal-store-contract.ts`, `support/execution-store-contract.ts` (in-memory + `execution-store.mongo.itest.ts`), `support/fencing-linearizability.ts`, `support/journal-mongo-world.ts`, `support/harness-mongo.ts`; Python `harness_core/journal/memory.py` (R3 M14: a test fake in a production package) and `tests/unit/harness_core/journal/test_memory_*_contract.py`. **No shared suite** for the journal tail (change stream), data keys, control-plane liveness or worker-feature sightings (R3 §3).

**Existing tests.** 53 files, 422 tests (unit 192, contract 158, integration 48, component 16, cohesion 7, fault 1 parametrized). This is the best-covered component. Weak: `mongo-execution-store.test.ts` asserts query shape against stubbed Mongoose (R3 §4); sweeper state transitions lack a table test.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| JRN-G1 | Journal history checker after every I/X test (JRN-22) | 5 | 3 | 15 | X | PR harness lane + nightly fault | shared `afterEach` in Node itests and the fault suite | none — **closed by QA-13** |
| JRN-G2 | Sweeper per-state deadline table, adding `recovering` and `needs_reconciliation` (JRN-18) | 4 | 3 | 12 | K | PR unit + PR harness lane | one table case in the execution-store contract | none — **closed by QA-13** |
| JRN-G3 | Relay over real Kafka and Redis Streams (JRN-15) | 4 | 3 | 12 | I | backend matrix (nightly) | outbox itest per broker | none |
| JRN-G4 | Model-based fast-check state machine + nightly differential on Mongo (JRN-23) | 5 | 2 | 10 | P | PR unit; nightly | `execution-store.model.test.ts` | none — **closed by QA-13** |
| JRN-G5 | Shared contract suites for journal tail, data keys, liveness, sightings (R3 §3) | 3 | 3 | 9 | K | PR unit + PR harness lane | four `*-contract.ts` | none |
| JRN-G6 | Commit latency histogram and SLO #1/#9 measured gates (JRN-24) | 3 | 3 | 9 | N-lat | weekly perf | bench `--profile short-turn --container` + histogram | none |
| JRN-G7 | Transient fold-read failure yields instead of failing (JRN-28) | 4 | 2 | 8 | U | PR unit | kernel test | none |
| JRN-G8 | Replace weak test: `mongo-execution-store.test.ts` query-shape asserts → behaviour on the Mongo itest (R3 §4) | 2 | 4 | 8 | I | PR harness lane | move assertions to the contract | none |
| JRN-G9 | `seq` in AAD (JRN-27) | 3 | 2 | 6 | U | PR unit | sealing test | product change |

## PRJ Projection, live bus and streaming

Built, owner HL. `backend/nodejs/apps/src/modules/agent-harness/projection/` (`run-projector.ts`, `stream-service.ts`, `transcript.ts`, `timeline.ts`), `adapters/redis/redis-live-bus.ts`, `adapters/mongo/mongo-journal-tail.ts`, Python publisher `backend/python/app/harness/livebus.py` and `app/harness/presentation.py`.

**Behaviours.**
1. B1 Journal + LiveBus deltas project to the legacy AG-UI frame vocabulary; `conversation_created` first; Node authors the terminal frame.
2. B2 Live equals reloaded: the parts a live client builds equal the transcript projected from the journal (N04 §7 #13/#17).
3. B3 Reconnect with `Last-Event-ID` continues with no gap and no duplicate; an expired buffer restarts the message from the journal.
4. B4 Frames from an older fence or an abandoned model attempt never reach the client.
5. B5 Audiences: owner sees everything; shared viewers, Slack and MCP never see tool arguments, approvals, steering, engine progress or unknown effects.
6. B6 LiveBus is at-most-once; the journal is authoritative; Redis down falls back to journal polling.
7. B7 Deterministic frames: a resumed stream replays identical frames and ids.
8. B8 Durable answers and legacy answers save the same conversation shape (rollback to legacy reads them).
9. B9 LiveBus delta → browser p95 < 100 ms (SLO #5); reconnect to first new frame ≤ 3 s (SLO #8).

**Levels.** U: projector, stream service, text filter, timelines. K: SSE and transcript goldens, durable-frames (live vs journal-only), replay-compat, rollback. C: stream over HTTP. I: Redis live transport. Es: browser reconnect. N-lat: live delta and reconnect.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| PRJ-01 | happy | Streams a run live to its end with legacy frames, then ends the response | U | in-memory live bus + journal | `projection/stream-service.test.ts` "streams a run live to its end…" |
| PRJ-02 | edge | Reconnect mid-message with `Last-Event-ID` (header or query) continues with exactly the rest | U/C | — | `stream-service.test.ts` "L-1"; `projection/stream.http.test.ts` "L-1" |
| PRJ-03 | edge | Expired buffer: reconnect ends the message and restarts from the journal; resume from any sent id yields the rest | U | — | `stream-service.test.ts` "L-2"; `run-projector.test.ts` "L-2" (both) |
| PRJ-04 | security | Frames from an older fence dropped and logged once per fence | U | — | `stream-service.test.ts` "L-3" |
| PRJ-05 | failure | A delta lost on the bus is logged as a gap; the stream still ends with the journal | U | — | `stream-service.test.ts` "a delta lost on the bus is logged as a gap…" |
| PRJ-06 | happy | Every durable scenario: live frames equal journal-only frames | K | goldens | `backend/nodejs/apps/tests/contracts/durable-frames.test.ts` |
| PRJ-07 | happy | Saved durable messages equal the legacy transcript; the MCP parser reads the durable stream | K | goldens | `contracts/durable-transcript.test.ts` |
| PRJ-08 | happy | Rollback: legacy GET returns what the run saved; legacy continues with the run's turns in its history | K | goldens | `contracts/durable-rollback.test.ts` |
| PRJ-09 | security | Approval cards, child approvals, unknown effects, steering and engine progress never reach shared viewers, Slack or MCP | U/K | — | `projection/run-projector-approvals.test.ts` "PJ-3"; `ux3-child-approvals.test.ts` "CA-4"; `ux3-unknown-effect.test.ts` "UE-2"; `contracts/durable-steering.test.ts` "shared viewers, Slack and MCP never receive it"; `run-projector-engine.test.ts` "PR-4: anyone but the owner…" |
| PRJ-10 | security | Stream route: a non-participant or another session gets 404; the read scope is required (execute-only token refused) | C | — | `projection/stream.http.test.ts` "L-15" (both) |
| PRJ-11 | happy | Deterministic frames: approvals, engine and unknown-effect frames replay identically on resume | U | — | `run-projector-approvals.test.ts` "PJ-4"; `run-projector-engine.test.ts` "PR-2…deterministic"; `ux3-unknown-effect.test.ts` "UE-3" |
| PRJ-12 | edge | Queued run: opened while queued, the stream says so right after `conversation_created`; frame ids kept after the queue | U | — | `projection/ux3-queued.test.ts` |
| PRJ-13 | failure | One `RUN_ERROR` with code and message ends the stream in error | U | — | `projection/run-projector-fail.test.ts` |
| PRJ-14 | happy | Redis live transport: read what the publisher wrote then follow in order; notify wakes the reader before the poll; probes `livebus=redis` | I | real Redis | `projection/live-transport.redis.itest.ts` |
| PRJ-15 | failure | Redis down at the publisher: raise once then back off without a timeout per frame; no notify connection → keep polling | U | — | `backend/python/tests/unit/harness/test_livebus.py::test_l5_redis_down_raises_once_then_backs_off…`; `projection/livebus.test.ts` "keeps polling a run when no notify connection can be opened" |
| PRJ-16 | happy | Python and Node agree on the LiveBus wire shape (shared vector) | K | vector | `test_livebus.py::test_the_wire_shape_matches_the_shared_vector`; `livebus.test.ts` "reads the wire shape pinned by the shared vector" |
| PRJ-17 | edge | Text filter: model-authored artifact markers and the confidence trailer never shown while streaming | U | — | `projection/text-filter.test.ts` |
| PRJ-18 | failure | Presentation: one failing stage keeps the result, artifacts and citation sources (R3 M6, FX) | U | — | `backend/python/tests/unit/harness/test_presentation_stages.py::test_m6_1_*` |
| PRJ-19 | edge | Property (fast-check): for random well-formed journals, the frontend live `parts[]` builder fed the projected frames equals the transcript projector's parts (TEST-STRATEGY §2.10) | P | fast-check, shared fixtures | **new** |
| PRJ-20 | edge | Attempt-discard: frames from an abandoned model attempt never reach output (REPORT §6.4) | U | — | **partial:** `stream-service.test.ts` "L-3" (fence only, not attempt within a fence) |
| PRJ-21 | edge | Reconnect resumes from the cursor without re-reading the whole journal (R1 §2.3 item 12) | U | journal read counter | **new** |
| PRJ-22 | failure | Browser reconnect survives a Node rolling restart: stream resumes within SLO #8 with no duplicate text (R1 F10, UX3) | Es/X | compose + restart | **partial:** `integration-tests/harness/fault/test_upgrades.py::test_ru4a_a_rolling_deploy_under_load_stalls_no_stream_past_the_bound` (no browser) |
| PRJ-23 | edge | LiveBus delta → SSE client p95 < 100 ms (SLO #5); reconnect storm (k6) first new frame ≤ 3 s (SLO #8) | N-lat | bench, k6 | **new** |
| PRJ-24 | security | Projections read only fields of the generated event schemas (no raw `data['…']` for 103 fields, R3 M15) | S | codegen types | **new** |
| PRJ-25 | happy | Activity bus wire (UX5): Python encodes and Node decodes every activity frame and key alike; a chat delta kind is not an activity frame | K | vector `activity-frames.json` | `backend/python/tests/unit/harness/test_ux5_activity_bus.py::test_ev2_*`; `backend/nodejs/apps/tests/modules/agent-harness/projection/ux5-activity-bus.test.ts` (UX5 EV-2) |
| PRJ-26 | happy | Activity stream (UX5): snapshot first, live `command`/`output`, a new snapshot per journal advance, `end` after the terminal event; a reconnect at any time is a fresh snapshot equal to the live state (journal tails + the bus's retained output); without Redis, snapshots only | C | in-memory journal + activity bus | `backend/nodejs/apps/tests/modules/agent-harness/ux5-activity-route.http.test.ts` (AP-2..AP-5, AP-8) |
| PRJ-27 | security | Activity routes (stream and full log) answer 404 to anyone whose view of the run is not the owner's, and `ACTIVITY_PANEL_DISABLED` when the org's Labs flag is off | C | — | `backend/nodejs/apps/tests/modules/agent-harness/ux5-activity-route.http.test.ts` (AP-6, AP-7) |
| PRJ-28 | edge | Command output never reaches the chat's live-bus key, so it cannot evict the text deltas a reconnecting chat needs | U | fakeredis | `backend/python/tests/unit/harness/test_ux5_activity_bus.py::test_bp4_*` (UX5 BP-4) |
| PRJ-29 | failure | A quarantined run's terminal frame is `RUN_ERROR` with code `quarantined`, the code `run-error-codes.json` lists and the web app words in every locale, whatever the cause (was the cause: `crash_loop`, `assembly_failed`) (PERF-A) | U | — | `backend/nodejs/apps/tests/modules/agent-harness/projection/run-projector.test.ts` (PERFA-9), `run-error-codes-vector.test.ts` |

**Fakes and contract suites.** `InMemoryLiveBus`, `InMemoryJournalTail`, goldens under `backend/nodejs/apps/tests/contracts/golden/`, `support/journal-fixtures.ts`. **No shared contract suite for the Redis live bus or the journal tail** (R3 §3); the Redis adapter has its own itest only.

**Existing tests.** 40 files, 241 tests (unit 198, contract 24, component 13, integration 4). Strong on audiences, determinism and reconnect at unit level. Missing the live == reload property, measured latency, and a browser-level reconnect across restarts.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| PRJ-G1 | Live == reloaded property over random journals (PRJ-19) | 4 | 3 | 12 | P | PR unit (frontend vitest + Node) | `transcript-parity.test.ts` with fast-check | none |
| PRJ-G2 | Browser reconnect across a Node restart (PRJ-22) | 4 | 3 | 12 | Es | nightly e2e | Playwright + `docker restart` | QA browser lane |
| PRJ-G3 | LiveBus contract over in-memory and Redis; journal tail contract (R3 §3) | 3 | 3 | 9 | K/I | PR unit + PR harness lane | `live-bus-contract.ts`, `journal-tail-contract.ts` | none |
| PRJ-G4 | Latency of deltas and reconnect (PRJ-23) | 3 | 3 | 9 | N-lat | weekly perf | bench profiles + k6 | none |
| PRJ-G5 | Cursor-based reconnect without full journal re-read (PRJ-21) | 2 | 4 | 8 | U | PR unit | read counter | product change |
| PRJ-G6 | Attempt-discard within a fence (PRJ-20) | 4 | 2 | 8 | U | PR unit | projector test with two attempts | none |
| PRJ-G7 | Typed projection fields (PRJ-24, R3 M15) | 2 | 3 | 6 | S | PR unit | tsc after codegen of event types | WP-11 |

## WRK Worker, claimer and loop kernel

Built, owner HL. `backend/python/app/harness/worker/claimer.py`, `app/harness/inline.py`, `app/harness/assembly/` (assembler, chat, deep), `backend/python/harness_core/kernel/durable/driver.py` (`_ClaimedRun`), `harness_core/kernel/fold.py`, `harness_core/kernel/agent/__init__.py` (`Agent.step`), `harness_core/failpoints.py`.

**Behaviours.**
1. B1 The claimer keeps polling and frees its slot whatever a claim raises; hints are acknowledged; a full worker acks without claiming; with the broker down runs are claimed by polling (R3 B1, FX-1).
2. B2 Fold is pure and deterministic; checkpoint + tail equals full replay; the cursor after a journaled `model.response` dispatches pending calls and never re-samples.
3. B3 Crash recovery by commit rule: before `model.response` re-samples; after it never; after an intent of an idempotent write retries with its key; a non-idempotent write becomes unknown (needs a person or a reconcile read).
4. B4 Pre-run steps (intent routing, goal rewrite, clarification) run once across crashes and reclaims.
5. B5 Assembly builds exactly the pinned grant; a tool gained after pinning is not exposed; a lost tool is `capability_lost` after bounded rechecks.
6. B6 Durable requests equal today's loop for the same inputs (C-24) and the parity inventory is complete (C-23).
7. B7 Cancel (Stop) interrupts the model call in flight and saves what streamed; cancelling `Agent.step` cancels its model call and guardrails (R1 F5, R3 M3, UX3, FX-5).
8. B8 Steering: queued input is consumed at the next tool boundary, in queue order, with attachments rendered once.
9. B9 Model failures: stall bounded by progress, retries as new attempts with no duplicate deltas, fallback journaled before the new route (flag), output bounded and a looping reply cut (P4.17).
10. B10 An unknown critical event or a newer fold version releases the claim; a worker never runs a spec it cannot read.
11. B11 Approval resume and cancel are notified, not polled (inline hold with `interaction_wakeup`, cancel wake) (R1 F9).

**Levels.** U: kernel, fold, driver, assembly with `ScriptedModel`, `ManualClock`, failpoints, in-memory journal. P: crash schedules over every single crash window; fold properties. K: durable vs legacy goldens (`tests/contracts/test_durable_*.py`), replay-compat both languages. replay: `tests/harness_replay`. X/N-chaos: `integration-tests/harness/fault` (crash points, upgrades, surface leaks) per broker nightly. gate-slow: C-24 every-case parity.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| WRK-01 | happy | Twenty runs progress together on one worker; a one-partition topic never serializes runs | U | in-memory journal | `backend/python/tests/unit/harness/test_run_claimer.py::test_o4_twenty_runs_progress_together_on_one_worker`, `test_o4_a_one_partition_topic_never_serializes_runs` |
| WRK-02 | failure | An unexpected claim error frees the slot and polling goes on; a poll failure outside the claim does not end the poller; a hint whose claim fails is acked and frees its slot (R3 B1) | U | fake journal | `test_run_claimer.py::test_fx1_3_*`, `test_fx1_5_*`, `test_fx1_4_*` |
| WRK-03 | failure | Broker down: runs claimed by polling | U | — | `test_run_claimer.py::test_o5_with_the_broker_down_runs_are_claimed_by_polling` |
| WRK-04 | happy | Cursor walks a two-turn run; approval suspends then resumes only the waiting call; an unknown effect waits for a human | U/P | hypothesis | `backend/python/tests/unit/harness_core/kernel/test_fold.py` |
| WRK-05 | failure | Crash before the response commit re-samples under a new message; after it never re-samples; after an intent executes the effect once | U | failpoints | `harness_core/durable/test_recovery.py::test_k1_*`, `test_k2_*`, `test_k3_*` |
| WRK-06 | failure | `kill -9` after the response commit resumes without calling the model; after an idempotent effect the retry is deduped; non-idempotent becomes unknown | U | subprocess failpoints | `harness_core/durable/test_kill9.py` |
| WRK-07 | failure | Every single crash window of a scripted run ends in the uncrashed result (crash schedules) | P | failpoints | `harness_core/durable/test_crash_schedules.py::test_every_single_crash_window` |
| WRK-08 | failure | A plain chat run resumes to the uncrashed answer; react pre-run runs once across a crash and reclaim | K | durable world | `tests/contracts/test_durable_chat_crash.py::test_pc4_*`; `test_durable_goldens.py::test_pr1_the_react_pre_run_runs_once_across_a_crash_and_a_reclaim` |
| WRK-09 | happy | Every durable model request is the legacy request (wave 1, harness cases, `auto`) | gate-slow/K | durable parity | `tests/cohesion/test_c24_durable_wire_parity.py`; `tests/contracts/test_durable_goldens.py::test_hk4_every_model_request_is_the_legacy_request` |
| WRK-10 | happy | Parity inventory: every legacy middleware, registration and hook is ported or says why | S | — | `tests/cohesion/test_c23_durable_parity_inventory.py` |
| WRK-11 | security | Assembly builds exactly the pinned grant; a tool gained after pinning is not exposed; a pinned tool lost is `capability_lost` | U | assembly world | `tests/unit/harness/assembly/test_assembler.py::test_d14_*`, `test_d9_a_pinned_tool_the_agent_lost_is_capability_lost` |
| WRK-12 | failure | A capability still lost after bounded rechecks fails the run; back within the bound resumes on the same pin | U | — | `harness_core/durable/test_needs_input_and_capabilities.py::test_pa7_*` |
| WRK-13 | edge | Unknown critical event releases the claim; unknown non-critical ignored; a spec from a newer fold version released; a lagging journal releases | U | — | `harness_core/durable/test_kernel.py` (four cases) |
| WRK-14 | happy | Stop cancels the model call in flight; the stopped answer is what streamed; a wake without cancel lets the call go on | U | — | `harness_core/durable/test_ux3_prompt_cancel.py`; `tests/unit/harness/test_ux3_stopped_answer.py` |
| WRK-15 | failure | Cancelling a turn cancels its model call and input guardrail and re-raises the outer cancellation (R3 M3) | U | — | `tests/unit/agent_loop_lib/agent/test_agent_step_cancellation.py::test_fx5_1_*` |
| WRK-16 | edge | Cancel mid-batch closes every dangling call; the next request is valid (paired tool_use/tool_result) | U | — | `harness_core/durable/test_barrier_cancel_yield_fence.py::test_k7_*` |
| WRK-17 | happy | Steering: input queued during a tool is consumed before the next model call; several in queue order; an answer streamed while input queued becomes a follow-up turn | U | — | `harness_core/durable/test_steering.py` |
| WRK-18 | failure | Steering attachments render once; a crash renders again only before the consumption committed | U | — | `harness_core/durable/test_steering_references.py::test_a_crash_renders_again_only_before_the_consumption_committed` |
| WRK-19 | failure | Model stall after partial output retried as a new attempt; three stalls fail as a timeout; no retry sleeps past the lease | U | `ManualClock` | `harness_core/durable/test_model_stall.py` |
| WRK-20 | failure | A stream cut mid-way is retried without duplicate deltas; retries bounded; 5xx falls back journaled before the new route's request (flag on) | U | — | `harness_core/durable/test_model_policy.py` |
| WRK-21 | edge | Output bound: a capped answer is continued and joined; a cut tool call is never run; a repetition cut is journaled and recovered | U | — | `harness_core/durable/test_output_bound_durable.py` |
| WRK-22 | happy | Approval within the hold runs without suspending; denied refuses and the model goes on; undecided suspends once and resolution resumes; no intent before the approval | U | — | `harness_core/durable/test_approval_hold.py` |
| WRK-23 | happy | Inline driver built with the cancel wake-up; a Redis failure in the wake degrades to a sleep | U | — | `tests/unit/harness/test_ux3_inline_wakeup.py`; `test_interaction_wakeup.py::test_a_redis_failure_degrades_to_a_sleep` |
| WRK-24 | edge | Drain at SIGTERM: a claim in flight is released before assembly; the drain is bounded and hands back what it cancels | U | — | `test_run_claimer.py::test_ru1a_*`, `test_ru1b_*` |
| WRK-25 | failure | Rolling deploy under load stalls no stream past the bound; a rollback leaves a newer run waiting for a newer worker | X | fault stack | `integration-tests/harness/fault/test_upgrades.py::test_ru4a_*`, `test_ru5a_*` |
| WRK-26 | happy | A recorded legacy run replays strictly; every scenario has a committed recording; a legacy recording replays on the durable path | replay | replay kit | `tests/harness_replay/test_replay_legacy_run.py`, `test_replay_durable.py` |
| WRK-27 | edge | Post-run step runs once with the ending; a crash after its start never re-runs it; a raising step never changes the ending | U | — | `harness_core/durable/test_post_run.py` |
| WRK-28 | edge | An idle worker imports no query, retrieval or LLM stack; worker RSS ≤ 350 MB idle (SLO #16; baseline 667 MiB PSS, over budget) | U/N-lat | `footprint.sh` | **partial:** `tests/unit/harness/test_worker_process.py::test_d7_the_idle_worker_imports_no_query_retrieval_or_llm_stack` (imports only, no RSS gate) |
| WRK-29 | edge | Approval resume is notified: from Allow to the next model call ≤ 500 ms p95 with Redis up (R1 F9 measured 1-5 s) | X/N-lat | harness stack | **new** |
| WRK-30 | failure | Claimer backoff resets on a run-ready notify; a queued interactive run is claimed within 1 s of admission when the hint is lost but the notify arrives (R1 §2.3 item 5, LAT1) | X | — | **new** (LAT1) |
| WRK-31 | edge | Hypothesis state machine (`RunStateMachine`: admit, claim, model_turn, tool_outcome, crash, lease_expire, approval, cancel, stale_commit, duplicate_dispatch, sweep) with the TEST-STRATEGY §2.1 invariants | P | hypothesis stateful | `tests/unit/harness_core/driver/test_run_state_machine.py` (QA-13) |
| WRK-32 | edge | Fold determinism across `PYTHONHASHSEED` values in a subprocess (REPORT C2) | P | — | `tests/unit/harness_core/kernel/test_fold_determinism.py` (QA-13) |
| WRK-33 | happy | Suggested next questions (UX5): an after-run step reserved while leased, called after the terminal commit (a slow model never delays `run.completed`), bounded at 384 output tokens, from the question and the final answer only; delivered as a `followups` frame and to Node; flag off, a child run, an unanswered run, a missed deadline or unusable output offer nothing and never change the ending | U | `RecordedTransport`, in-memory ledger | `backend/python/tests/unit/harness/test_ux5_followups.py`, `backend/python/tests/unit/harness_core/durable/test_ux5_post_run_events.py` (FU-1..FU-4) |
| WRK-34 | edge | `pack`: a step's events as consecutive commits within the limit; order kept, a unit never cut while a commit can hold it, a part's slots one `loop.state_saved` at its end (PERF-A) | U | — | `backend/python/tests/unit/harness_core/durable/test_perfa_commit_parts.py` |
| WRK-35 | happy | One model turn of 50, 51, 100, 101 and 500 parallel reads completes: every read one intent and one outcome, consecutive commits of one attempt under one fence (PERF-A, QA22-F1) | U, I | in-memory journal; Node's server on the in-memory store and on Mongo | `backend/python/tests/unit/harness_core/durable/test_perfa_wide_reads.py::test_perfa_4_*`; `backend/python/tests/unit/harness/test_perfa_wide_reads_node.py` |
| WRK-36 | failure | A crash after each part of a split reads commit: no read lost or journaled twice, the transcript equals the crash-free run's; a read that sets slots, answers or asks the user still ends the run the same way; a completion Node refuses for queued input repeats no outcome (PERF-A) | U | failpoint `worker.commit.after_ack` at every split point | `backend/python/tests/unit/harness_core/durable/test_perfa_wide_reads.py::test_perfa_5_*`, `::test_perfa_6_*` |
| WRK-37 | edge | A run ended (cancelled, failed, quarantined) with limit − 1, limit, limit + 1 and 500 open calls ends in one commit within the limit; a call whose effect may have been applied keeps its outcome; the next run's request pairs every call (PERF-A, QA22-F1) | U, I | in-memory journal; Node's server | `backend/python/tests/unit/harness_core/durable/test_perfa_terminal_bound.py::test_perfa_7_*`; `backend/python/tests/unit/harness/test_perfa_wide_reads_node.py::test_perfa_7_*` |
| WRK-38 | failure | A commit the journal permanently refuses fails the run on that attempt; when the terminal commit's events are refused the transition alone is committed; a run past the attempt cap is quarantined even then (the cap did not catch QA22-F1 because its quarantine commit had the same unbounded shape) | U | a journal that refuses an event type, or every commit with events | `backend/python/tests/unit/harness_core/durable/test_perfa_terminal_bound.py::test_perfa_8_*` |
| WRK-39 | edge | The other commits that grow with the model's parallel calls stay within the limit: a response presented with one event a call (journaled whole first; a resumed attempt completes its presentation), approvals asked for more writes than fit (each request with its reason), a batch of refused delegations; what rode with a landed part is not journaled again (PERF-A) | U | in-memory journal, test presenter | `backend/python/tests/unit/harness_core/durable/test_perfa_other_kinds.py` |

**Fakes and contract suites.** `harness_core/testing/` (`scripted_model.py`, `clock.py`, `failpoints.py`, `durable.py`, `kernels.py`, `replay/`), `harness_core/journal/memory.py`, `tests/unit/harness/assembly/world.py`, `tests/harness_replay/durable_driver.py`. **Twelve ports in `harness_core/kernel/durable/ports.py` have no shared contract** (R3 §3: `DurableToolPort`, `RunPresenter`, `RunRestore`, `TurnShaper`, `LoopDirector`, …); `tests/unit/harness_core/engines/durable/test_port_contracts.py` covers the engine kernel's ports only.

**Existing tests.** 108 files, 657 tests (unit 528, contract 56, replay 40, fault 9, gate-slow 9). The crash and parity layers are strong. Weak tests (R3 §4): `test_run_claimer.py:287` sleeps 50 ms then asserts the run is still queued (sleep-then-assert-nothing), `:315-317` wall-clock bound `< 1.0 s`; `tests/harness_replay/test_dur2_stream_assembly.py` wall-clock assert; unspecced graph mocks in `tests/unit/harness/assembly/world.py:309` and `tests/harness_replay/durable_driver.py:100-122`; `_deployment` fixture copied in 17 files of `tests/unit/harness/assembly/` (no conftest); 159 private-attribute reads across harness tests.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| WRK-G1 | Run state machine with invariants (WRK-31) | 5 | 3 | 15 | P | PR unit (ci profile), nightly 5,000 examples | `tests/unit/harness_core/driver/test_run_state_machine.py` | none — **closed by QA-13** |
| WRK-G2 | Approval resume and claim latency measured (WRK-29, WRK-30, R1 F9) | 3 | 4 | 12 | X/N-lat | PR harness lane (bound), weekly perf | stack test timing Allow → next model call | LAT1 |
| WRK-G3 | Replace weak tests: sleeps and wall-clock bounds in `test_run_claimer.py:287,315`, `test_dur2_stream_assembly.py` (R3 §4) | 3 | 4 | 12 | U | PR unit | wait on events; `ManualClock` | none |
| WRK-G4 | Contract suites for the 12 durable kernel ports (R3 §3) | 3 | 3 | 9 | K | PR unit | `harness_core/testing/contracts/durable_ports.py` + C-2 registration | none |
| WRK-G5 | Fold determinism across hash seeds (WRK-32) | 4 | 2 | 8 | P | PR unit | subprocess property | none — **closed by QA-13** |
| WRK-G6 | Replace weak tests: spec'd graph mocks in `assembly/world.py`, `durable_driver.py`; one `assembly/conftest.py` for `_deployment` (R3 §4, WP-15) | 2 | 4 | 8 | U | PR unit | `AsyncMock(spec=IGraphDBProvider)` or `tests/support/fake_record_graph.py` | none |
| WRK-G7 | Idle worker RSS gate (WRK-28, SLO #16) | 2 | 4 | 8 | N-lat | release checklist / nightly footprint | `footprint.sh` harness-on check | product change |

## CHD Child runs and deep mode

Built, owner HL. Node `backend/nodejs/apps/src/modules/agent-harness/application/child-run-service.ts`, `domain/child-runs.ts`, `adapters/mongo/mongo-child-runs.ts`; Python `backend/python/app/harness/assembly/deep.py`, the durable driver's delegation, `harness_core/journal` child grants.

**Behaviours.**
1. B1 A child is admitted with its pinned spec, both streams and the parent's wake in one transaction; the same invocation replays its child; `maxChildren` bounds new children.
2. B2 A child's grant is the parent's pin narrowed (tools, MCP servers following tools, knowledge, budget share, `maxTokens`, workspace fork); an engine child narrows its engine parent's profile.
3. B3 The parent suspends on `join`, is woken exactly once when its children end, and never admits a child twice across crashes (before suspend, after spawn, after the dispatch turn).
4. B4 A child's approval and unknown effect reach the owner in the main thread, attributed to the child (R1 F3, UX3); shared viewers get none.
5. B5 Cancelling the parent cancels its children; a quarantined child is a failed result and the parent answers.
6. B6 Deep mode phase machine matches legacy (plan, critique, dispatch, verify) with the same requests.
7. B7 Budgets: an unlimited tree charges every call to the root; children cannot exceed the root cap; the run that cannot pay ends named.
8. B8 Workspace forks merge at the join once (keep_target, conflicts reported).
9. B9 A child inherits its parent's taint label (UX2); a grandchild gets its own parent's label.

**Levels.** U: Python kernel delegation, deep phases, Node child domain. K: child-run contract (in-memory + Mongo), durable deep goldens and recovery. C: child admission HTTP. I: Mongo races on join. X: fault suite for deep runs (planned).

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| CHD-01 | happy | Admits the child, its pinned spec, both streams and the wake in one transaction | K | child-run contract | `support/child-run-contract.ts` "CA-1" |
| CHD-02 | edge | Same invocation replays its child and writes nothing; another request conflicts; `maxChildren` bounds new children, never a replay; a run granted no children is refused | K | — | `support/child-run-contract.ts` "CA-2", "CA-3" (both) |
| CHD-03 | security | The child spec is the parent pin narrowed; MCP servers follow granted tools; a shared session's child does not inherit the parent's history audience | U | — | `child-runs.test.ts` "CS-1", "CG-8", "SS2 FW-3" |
| CHD-04 | security | Engine child narrows its engine parent; a widening refused naming `executor`; a loop parent's engine child needs the org's engine authority | U | — | `child-runs-executor.test.ts` "CH-1" cases |
| CHD-05 | concurrency | Either the parent is woken or its join is refused `JOIN_READY`, over many races; a failure after the child insert leaves no child, event, wake or counter | I | Mongo RS | `execution-store.mongo.itest.ts` (first two) |
| CHD-06 | failure | Parent dies after spawning rejoins without spawning again; dies before suspend joins next attempt; child dies → reclaimed, wakes parent once; parent dies after the dispatch turn admits each child once | K | durable deep world | `tests/contracts/test_durable_deep_recovery.py::test_cr1_*`, `test_cr1b_*`, `test_cr2_*`, `test_cr5_*` |
| CHD-07 | failure | Cancelling the parent cancels its children; a quarantined child is a failed result and the parent answers | K | — | `test_durable_deep_recovery.py::test_cr3_*`, `test_cr4_*` |
| CHD-08 | happy | Deep: every agent's requests and the answer are legacy's; the legacy deep stream matches its golden | K | goldens | `tests/contracts/test_durable_deep_goldens.py` |
| CHD-09 | edge | Deep phase gate refuses a spawn before the plan is approved; fan-out per turn capped; a succeeded task is not dispatched again | U | — | `tests/unit/harness/assembly/test_deep_delegation.py::test_dd1_*`, `test_dd6_*`, `test_dd3_*` |
| CHD-10 | happy | A child's approval reaches the owner attributed to the child; announced once on a late subscription; closes as expired when the child ends | U | — | `projection/ux3-child-approvals.test.ts` "CA-1", "CA-2", "CA-4" |
| CHD-11 | happy | Approved in the hold runs once then the parent joins; child ends while the approval is suspended then the decision resumes | U | — | `harness_core/durable/test_children_and_approvals.py` |
| CHD-12 | edge | Budgets across the tree (unlimited → root; capped root; the run that cannot pay ends named) | K | ledger fake | `tests/contracts/test_durable_deep_budget.py` |
| CHD-13 | happy | A later wave sees both files of the earlier wave; a merge conflict reaches the orchestrator; merges once across a crash | K/U | — | `test_durable_deep_forks.py`; `harness_core/durable/test_child_forks.py::test_km4_*`, `test_km5_*` |
| CHD-14 | security | Child inherits its parent's label; a child of a parent that read nothing posts without asking; a grandchild gets its own parent's label | U | — | `tests/unit/harness/test_ux2_loop_skew_and_children.py::test_vc1_*`, `test_vc4_*`; `ux2-child-label.test.ts` |
| CHD-15 | security | A sub-agent definition is pinned in the child spec and part of the request; a parent reads its child's sealed content, another session does not | C | — | `child-runs.http.test.ts` "NK-1", "CH-3" |
| CHD-16 | edge | Deep wave dispatch is notified, not polled: each wave adds ≤ 200 ms over the model time (R1 F13 measured +1-4 s per wave) | X/N-lat | harness stack | **new** (LAT1) |
| CHD-17 | failure | Fault suite: SIGKILL the worker mid-wave in a deep run on the real stack; each child admitted once, parent answers once | X | fault stack | **new** |
| CHD-18 | security | A loop parent's engine child includes the agent's own `engineProfile` layer (R2 F5) | U | — | `backend/nodejs/apps/tests/modules/agent-harness/child-runs-engine-authority.test.ts` NO-6 (CPS, merged at `a168c7d5c`) |
| CHD-19 | edge | Python and Node agree on the deepest level and child run ids (table) | K | — | `tests/unit/harness_core/journal/test_child_grants.py::test_table_and_build_agree_on_the_deepest_level`, `test_child_run_id_matches_node`; `child-runs.test.ts` "the table and this build agree…" |
| CHD-20 | edge | One phase machine: deep mode reuses `phase_driver` constants; no third copy (R3 D8) | S/U | — | **new** (WP-12) |

**Fakes and contract suites.** `support/child-run-contract.ts` (in-memory + Mongo), `harness_core/testing/contracts/children.py`, `test_memory_children_contract.py`, `tests/contracts/deep_scenarios.py`.

**Existing tests.** 20 files, 153 tests (unit 104, contract 46, component 3). Strong recovery and parity; no real-stack fault run of deep mode and no wave latency measurement.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| CHD-G1 | Engine child skips the agent layer (CHD-18, R2 F5) | 5 | 4 | 20 | U | PR unit | child authority test | **closed by CPS (NO-6)** (`a168c7d5c`) |
| CHD-G2 | Wave latency notified (CHD-16, R1 F13) | 3 | 4 | 12 | X/N-lat | PR harness lane (bound) | stack timing | LAT1 |
| CHD-G3 | Deep mode on the real stack under SIGKILL (CHD-17) | 4 | 2 | 8 | X | nightly fault | crash point in `test_crash_points.py` for a deep scenario | none |
| CHD-G4 | One phase machine (CHD-20, R3 D8) | 2 | 3 | 6 | U | PR unit | refactor kept green by `test_deep_phases.py` | WP-12 |

## CTX Context compiler

Built, default off (`HARNESS_CONTEXT_COMPILER`), owner HL. `backend/python/harness_core/context/` (compiler, ladder, compaction, notes), `harness_core/kernel/context/`, wiring in `backend/python/app/harness/assembly/`. P4.16 kept it off on the cost clause; P4.17 bounds output, which projects the clause to pass.

**Behaviours.**
1. B1 Off: every request is byte-equal to the turn shaper's (flag-off bytes).
2. B2 On: bands 1-3 byte-stable under append; tools sorted by tool id independent of hash seed; a newly granted tool appended, never sorted in; volatile content only in the tail.
3. B3 Ladder: ≤ 50% nothing; > 50% clears old results once with a recoverable label; > 80% compacts through a metered summarising call; never out of order; every reduction journaled once.
4. B4 Every rendered tool_use has exactly one tool_result whatever the clear and compact ranges.
5. B5 Provider layouts: Anthropic breakpoints at band edges; OpenAI no markers, same stable order; the run-start note after the input on every wire (P4.15).
6. B6 Request too large: older turns compacted; newest results halved until they fit; at the floor the run fails; a resumed attempt sends the journaled cap.
7. B7 Information parity: the compiled twin carries every element the shaper sends (images, artifacts, calls, order).
8. B8 Other principals' and external content is quoted, never instruction-shaped; memories are the tail's first part, never cached.
9. B9 Cache-read share is not worse than the flag-off baseline per model (SLO #11).

**Levels.** U/P: compiler, ladder, pairing properties. K: information parity, long sessions, durable compiler contract. El: SLO #11.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| CTX-01 | happy | Compiler off: requests byte-equal to before | U | — | `harness_core/context/test_flag_off_bytes.py::test_fl2_*` |
| CTX-02 | edge | Twenty turns and a second run only append; a tool granted mid-run appended to the band; after a compaction the prefix is stable again | P | hypothesis | `harness_core/context/test_cache_stability.py::test_cc1_*`, `test_cc3_*`, `test_cc8_*` |
| CTX-03 | edge | Tools sorted by tool id whatever the insertion order and hash seed; a removed tool drops out without reordering | P | — | `harness_core/context/test_compiler.py::test_cc2_*`, `test_cc3_*` |
| CTX-04 | happy | Ladder thresholds: below half nothing; above half clears once; above 80% compacts; a rising count clears then compacts | U | — | `test_ladder.py`; `test_compiler_kernel.py::test_ld1..ld4` |
| CTX-05 | edge | Any clear and compact ranges keep every call paired; the guard refuses an unpaired list | P | — | `test_pairing.py::test_cc10_*`, `test_cc11_*` |
| CTX-06 | happy | Compaction call reserved and charged to the run's account; budget exhausted fails the run and compacts nothing | U | ledger fake | `test_compaction_metered.py` |
| CTX-07 | failure | Summarising call returns empty → `compaction_failed`; truncation is deterministic | U | — | `test_compaction_strategies.py` |
| CTX-08 | failure | Request too large: compact older turns; halve the newest results cap until it fits; fail at the floor; resume sends the journaled cap | U | — | `test_request_too_large.py::test_l8_1..l8_4` |
| CTX-09 | happy | Provider layouts: Anthropic breakpoints at band edges; OpenAI no markers same order | U | goldens | `test_provider_layouts.py` |
| CTX-10 | happy | The note follows the input on Messages, Chat Completions, Converse and Responses wires | U | — | `test_note_wire.py::test_af7_*` |
| CTX-11 | happy | Information parity: each element dropped from the compiled twin is reported; images, artifacts, calls and order checked | K | — | `tests/contracts/test_compiler_information_parity.py` |
| CTX-12 | happy | Long sessions: the ladder fires and the run keeps what it needs; compiler off runs the legacy shapers | K | — | `tests/contracts/test_compiler_long_sessions.py` |
| CTX-13 | happy | A PipesHub run with the compiler answers with banded requests; react starts from its goal; shaper and compiler send the same goal text | K | — | `tests/contracts/test_durable_context_compiler.py` |
| CTX-14 | security | Memories are the tail's first part on every turn and never a cached band | U | — | `harness_core/memories/test_compiler_band.py::test_cb1_*` |
| CTX-15 | security | Runtime VFS services are excluded from context serialization | U | — | `tests/unit/harness/test_context_security.py` |
| CTX-16 | security | Content from another principal (collaborator turn, MCP result, connector record) is quoted with provenance and cannot close the quote (injection through a closing delimiter) | U/P | hypothesis strings | `tests/unit/harness_core/context/test_quoting_property.py::test_ctx16_*` (QA-27) |
| CTX-17 | edge | `compile(fold(journal)) == recorded model.request.contextDigest` for every replay-corpus run (recompile from journal alone, TEST-STRATEGY §2.2) | replay | replay kit | `tests/harness_replay/test_compiler_replay.py` (QA-13: each golden scenario resumed before each model turn) |
| CTX-18 | edge | Every `[cleared: see /tool-results/...]` marker names a path the tool-result provider reads back byte for byte | U | — | **new** |
| CTX-19 | happy | Wiring: off by default; a malformed value refused; on uses the model's window and compaction runs on its transport | U | — | `tests/unit/harness/assembly/test_context_compiler_wiring.py` |
| CTX-21 | edge | Cache-read share with the compiler on ≥ flag-off on the same live model (SLO #11) | El | production | **partial:** the production metric `pipeshub_agent_harness_run_cache_read_ratio`, not a gate |
| CTX-22 | edge | Compiler on in the e2e covering-array rows across every wire (factor `compiler`), journaled ladder events visible to admins | Es | e2e lane | **new** |

**Fakes and contract suites.** `harness_core/testing/contracts/compaction.py` (CompactionStrategy contract), `ScriptedModel`, usage-sequence generators in the ladder tests, `tests/evals/cache_sim.py`.

**Existing tests.** 24 files, 168 tests (unit 92, contract 25, eval 51). Weakness: nothing exercises the compiler on the real stack, and the provenance quoting has no adversarial property.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| CTX-G1 | Quoting resists delimiter injection (CTX-16) | 5 | 2 | 10 | P | PR unit | hypothesis over adversarial strings | none — **closed by QA-27** |
| CTX-G2 | Compiler on the real stack (CTX-22) | 3 | 3 | 9 | Es | nightly e2e | covering-array rows with `compiler=on` | QA browser lane |
| CTX-G3 | Recompile-from-journal digest equality over the corpus (CTX-17) | 4 | 2 | 8 | replay | PR replay | `tests/harness_replay/test_compiler_replay.py` | none — **closed by QA-13** |
| CTX-G4 | Cleared-result markers read back (CTX-18) | 3 | 2 | 6 | U | PR unit | provider round trip | none |
| CTX-G5 | SLO #11 as a live gate before default-on (CTX-21) | 3 | 2 | 6 | El | live (release) | registered live run per default model | budget |

## MGW Model gateway and transports

Built, owner HL. Transports `backend/python/harness_core/models/` (OpenAI chat and Responses, Anthropic, Gemini, Bedrock/SigV4, Azure, Ollama), `models/output_bound.py`, `models/policy.py`, `models/provider_state.py`; gateway `harness_core/gateway/model_gateway.py`, `*_wire.py`, `token.py`; process `backend/python/app/model_gateway_main.py`; Node token issuance `application/gateway-token-*.ts`.

**Behaviours.**
1. B1 Each wire (Chat Completions for opencode, Responses for Codex, Messages for Claude Code) parses the engine's real requests and refuses unknown parameters, stateful Responses and off-allowlist betas in the wire's error shape.
2. B2 Gateway tokens bind run, attempt and routes digest; expired, unknown-kid (after one refresh), other binding, provider key headers and model names naming another target are refused before any upstream call (SEC1).
3. B3 Budgets refuse before the provider; a streamed call is charged once; reservations sized at the call's output limit (P4.17, P6.5).
4. B4 Every model call carries an output limit on the wire (32,768 turns/engine, 8,192 structured), capped at the model's maximum; a looping reply is cut and recovered (P4.17).
5. B5 Transports report usage including cache read/write once; retry classification consistent across providers (R3 minor: OSError, Gemini by text).
6. B6 Stalls are detected by progress, not wall time; a trickle is never cut.
7. B7 Model policy (flag): retries as new attempts, fallback to a pinned route journaled first (`model.fallback`); a resume continues on the journaled route with provider-valid history.
8. B8 Provider state (flag): thinking signatures and reasoning items captured and replayed on their own wire only; off is byte-equal.
9. B9 Refusals are reported once per reason to the run's audit (`gateway.refused`), parameters by path never value.
10. B10 413 and unserved routes answer in each wire's shape.

**Levels.** U: transports with recorded HTTP shapes, wires, gateway, tokens. C: gateway servers with the real OpenAI and Anthropic SDKs over HTTP (`test_*_server.py`). K: refusal-sink contract, families. El: live Azure lanes (`test_live_azure_*`, marker-gated). N-lat: gateway overhead (not measured).

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| MGW-01 | happy | Chat wire parses opencode's recorded request; refused parameters and invalid requests in the OpenAI error shape | U | recorded requests | `harness_core/gateway/test_chat_wire.py::test_cw1..cw3` |
| MGW-02 | happy | Responses wire parses Codex's requests; stateful requests refused; `store:false` served | U | — | `test_responses_wire.py::test_rw1..rw3` |
| MGW-03 | happy | Messages wire parses Claude Code's requests; betas off the allowlist refused by name | U | — | `test_messages_wire.py::test_mw1..mw3` |
| MGW-04 | happy | OpenAI SDK talks to the gateway; a recorded opencode request streams SSE; real transports see no engine header | C | HTTP server | `test_model_server.py::test_hs1..hs3` |
| MGW-05 | happy | Anthropic SDK reads both answers; Claude Code's requests replay over HTTP | C | — | `test_messages_server.py::test_mh1..mh3` |
| MGW-06 | security | Bad tokens refused before anything; models lists exactly the grant | U | — | `test_model_gateway.py::test_mg3_*`, `test_mg4_*` |
| MGW-07 | security | Caller provider-key header refused; provider key in place of the gateway token refused; body fields cannot steer the upstream; model name cannot name another target | U | — | `test_sec1_model_exfiltration.py::test_mx1..mx4` |
| MGW-08 | security | Token: valid names its binding; expired refused; unknown kid refreshes once then refused; rotation on another replica picked up | U | — | `test_token.py::test_gt1..gt3` |
| MGW-09 | security | Node token issuance: worker credential gets a token bound from Node's records; attempt token or wrong credential 401; another attempt or lapsed lease 409 `RUN_NOT_LEASED` | C | — | `gateway-token.http.test.ts` "NA-6" cases |
| MGW-10 | happy | Budgets refuse before the provider; streaming charged once under the message/response id | U | ledger fake | `test_messages_gateway.py::test_mg4_*`, `test_mg2_*`; `test_responses_gateway.py::test_rg2_*` |
| MGW-11 | happy | Output limit: every wire sends the engine's limit capped; with limits off only the engine's own; reservation sized at the call's limit; looping upstream ends with the wire's max-tokens stop | U | — | `test_gateway_output_limit.py::test_gw1..gw3` |
| MGW-12 | happy | Each purpose has its limit capped by the model; chat sends `max_completion_tokens`, Responses `max_output_tokens`, Messages `max_tokens` with thinking inside | U | — | `models/test_output_bound.py`, `test_output_limit_wire.py` |
| MGW-13 | edge | Stream watch: never starts → stall on first event; a trickle never cut; a long silence before the first event is not a stall | U | `ManualClock` | `models/test_stream_watch.py` |
| MGW-14 | happy | Model policy routes in pinned order; failures map to triggers; refusals never do | U | — | `models/test_policy.py::test_mp1_*`, `test_mp2_*` |
| MGW-15 | happy | Provider state: Anthropic thinking and redacted blocks captured in order and sent back verbatim first; off nothing captured; flag-off requests unchanged for every transport | U | — | `models/test_provider_state.py`; `test_flag_off_requests.py` |
| MGW-16 | security | Bedrock SigV4 signs exactly as botocore; the secret never appears in headers or repr | U | AWS test vectors | `models/test_sigv4.py` |
| MGW-17 | security | Refusal report with the run token sent once with ids and codes; a parameter reported by path never value; a flood is one report | U/K | refusal sink contract | `test_sec2_refusal_reports.py`, `test_sec2_refusal_sink_contract.py` |
| MGW-18 | failure | Oversized body and unknown path refused in each wire's envelope | U | — | `test_sec2_wire_errors.py` |
| MGW-19 | happy | Live Azure: stream with a tool on Chat, Responses and Messages; Claude Code binary runs a tool on Azure through the gateway | El | live keys | `tests/unit/harness/access/test_live_azure_gateway.py`, `test_live_azure_messages.py`, `test_live_azure_responses.py` |
| MGW-20 | edge | One retryable status set and one error classifier across transports: `OSError`, 429, 5xx and timeouts classify the same for OpenAI, Anthropic, Gemini, Ollama, Azure (R3 minor, WP-5) | U | table test | **new** |
| MGW-21 | edge | Gemini and Ollama transports: usage, tool calls and streaming parse recorded cassettes like the other transports (no dedicated tests in inventory) | U | `respx` cassettes | **new** |
| MGW-22 | failure | Gateway settles a reservation with backoff on transient ledger errors (R3: `_settled` retries with no backoff, `model_gateway.py:625-645`) | U | ledger fake with failures | **new** |
| MGW-23 | edge | A resumed run after fallback continues on the journaled route with provider-valid history (normalized ids, no foreign thinking) | U | — | `harness_core/durable/test_routing.py::test_mp9_*`; `kernel/test_fold_model_route.py` |
| MGW-24 | edge | Gateway non-model overhead ≤ 25 ms p50 per call on the CI stack | N-lat | bench | **new** |
| MGW-25 | security | Production `assert` statements used as control flow in `model_gateway.py:319,363` removed; the same inputs under `python -O` still refuse | U | run under `-O` | **new** (R3 minor) |

**Fakes and contract suites.** `harness_core/testing/model_gateway.py`, `gateway.py`, `cloud_providers.py`, `model_script.py`, `ScriptedModel`; `harness_core/testing/contracts/gateway.py`. `NodeGatewayTokenSource` does not run under the `TokenSource` contract (R3 §3).

**Existing tests.** 41 files, 317 tests (unit 309, component 7). Thorough on wires, security and output limits. Weak test: `test_sec2_refusal_reports.py:110-123` asserts `time.monotonic() - started < 1.0` (wall clock, R3 §4).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| MGW-G1 | Uniform retry classification across transports (MGW-20) | 3 | 4 | 12 | U | PR unit | table test over transports | WP-5 |
| MGW-G2 | `NodeGatewayTokenSource` under the `TokenSource` contract (R3 §3) | 3 | 3 | 9 | K | PR unit | contract subclass | WP-3 |
| MGW-G3 | Gemini/Ollama cassette tests (MGW-21) | 3 | 3 | 9 | U | PR unit | recorded responses | none |
| MGW-G4 | Replace weak test: wall-clock in `test_sec2_refusal_reports.py:110-123` (R3 §4) | 2 | 4 | 8 | U | PR unit | `ManualClock` | none |
| MGW-G5 | Settle backoff (MGW-22) | 3 | 2 | 6 | U | PR unit | ledger fault fake | product change |
| MGW-G6 | `assert` as control flow (MGW-25) | 3 | 2 | 6 | U | PR unit | `-O` run | product change |
| MGW-G7 | Gateway overhead measured (MGW-24) | 2 | 3 | 6 | N-lat | weekly perf | bench profile | none |

## TGW Tool gateway, effects and invoker

Built, owner HL. `backend/python/harness_core/effects/` (runner, log, policy, flow, reconcile), `harness_core/invoker/`, `harness_core/tools/` (executor, registry, time bound), `harness_core/gateway/endpoint.py` (`RunToolEndpoint`), `backend/python/app/harness/gateway/host.py` and `mcp.py` (the engines' MCP transport on port 8098).

**Behaviours.**
1. B1 Intent is journaled before a write executes and the outcome after; a read rides with its outcome (commit rules).
2. B2 Policy decides `allow | ask | deny | unavailable` before execution; deny records a refusal and never executes; unclassified tools are writes; MCP tools are writes whatever the server claims.
3. B3 An ungranted tool never runs under any spelling (case, prefix, instance tag, repaired name); a restricted registry repairs names only to granted tools.
4. B4 Recovery by effect class: read re-runs; idempotent write retries with key `org:run:invocation`; reconcilable write looks up first; external write becomes `tool.unknown`, never executed twice.
5. B5 A write that times out or may have landed is unknown, not a retryable failure; a definite error stays an error.
6. B6 Every tool call is time- and stall-bounded; a stalled call ends the call, not the run.
7. B7 An approval for other arguments or forged approval arguments does not carry over; an identical recall answers from the log.
8. B8 The engine endpoint needs the run's gateway token bound to this attempt; a user session JWT is not a gateway token.
9. B9 A disconnecting caller does not cut an effect between intent and outcome (shielded invoke).
10. B10 `caller=code` (run_code SDK) and `caller=model` get identical decisions (TEST-STRATEGY §2.3).
11. B11 Every reachable tool states its effect and sink (`sink` tag); the descriptor check fails on an untagged tool.

**Levels.** U: runner, invoker, policy, executor. K: effect-log, tool-catalog and endpoint contracts. P: policy decision table and properties (planned). integration-docker: E2E3 tool matrix on real engines. X: fault crash points for writes.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| TGW-01 | happy | Intent before execute and outcome after; policy deny records a refusal and does not execute; ask without a hold seam is held with nothing recorded | U | — | `harness_core/effects/test_runner.py::test_er1..er4` |
| TGW-02 | happy | Invoker: a granted read executes and is recorded; a write records intent before running; an ungranted call is refused and never runs; an identical recall of a write answers from the log | U | — | `harness_core/invoker/test_invoker.py::test_iv1..iv5` |
| TGW-03 | security | Every spelling of an ungranted tool is not granted and never runs (G2 T1-T5) | U | — | `tests/unit/harness/gateway/test_g2_gateway_attempts.py::test_g2_t1_t5_*` |
| TGW-04 | security | An approval-needing write is held whatever the call claims; forged approval arguments are a new interaction; an approval for other arguments does not carry over | U | — | `test_g2_gateway_attempts.py::test_g2_p1_p2_*`, `test_g2_p3_*`, `test_g2_p4_*` |
| TGW-05 | failure | Non-idempotent write that may have landed is unknown and never runs again; an idempotent write retried then unknown; a read is an error never unknown; a definite write error runs again | U | — | `harness_core/invoker/test_registry_unknown.py::test_am8..am10`; `harness_core/tools/test_executor_unknown.py` |
| TGW-06 | security | A restricted registry holds only the named tools; name repair resolves only to granted tools | U | — | `harness_core/tools/test_registry_restricted.py` |
| TGW-07 | failure | A call that awaits forever ends at its wall bound; one spinning on the loop is interrupted; the run goes on | U | — | `harness_core/tools/test_time_bound.py::test_dur2_t4_*`, `test_dur2_t5_*` |
| TGW-08 | security | Engine MCP transport: list and call through the SDK client; requests without a gateway token 401; a user session JWT is not a gateway token | C | — | `tests/unit/harness/gateway/test_mcp_transport.py::test_mt1_*`, `test_mt3_*` |
| TGW-09 | security | Binding: this attempt's token accepted; a token for another binding refused; grant digests match Node's | U | — | `harness_core/gateway/test_binding.py` |
| TGW-10 | failure | A disconnect does not cut an effect between intent and outcome (shielded invoke) | K | endpoint contract | `harness_core/gateway/test_endpoint_contract.py::test_a_disconnect_does_not_cut_an_effect_between_intent_and_outcome` |
| TGW-11 | happy | Progress every interval while held; each refusal logged once with its code | K | — | `test_endpoint_contract.py::test_ep9_*`, `test_ep16_*` |
| TGW-12 | happy | Every connector tool carries one reviewed effect tag; every reachable tool states its effect; MCP tools are writes | S/U | — | `tests/unit/harness/test_tool_effects.py::test_ro1_*`; `test_effect_tags_complete.py::test_et1_*`, `test_et4_*` |
| TGW-13 | edge | `run_sql`-style classifier: a plain SELECT is a read, anything else a write; a failing classifier leaves the call a write | U | — | `tests/unit/harness/test_call_effects.py::test_c3_*`; `harness_core/durable/test_call_effects.py::test_c3_a_failing_classifier_*` |
| TGW-14 | happy | Effect log contract: commits use the run's command ids | K | effect-log contract | `harness_core/effects/test_effect_log_contract.py` |
| TGW-15 | happy | One `execute_tool` span per executed call; refused calls carry their reason code; arguments only when the operator opts in | U | in-memory tracer | `harness_core/gateway/test_tool_tracing.py::test_ot4_*`, `test_ot9_*` |
| TGW-16 | happy | Engines × tool families on real components: knowledge on Neo4j and ArangoDB, MCP through a runner, an unapproved or disabled MCP tool never runs, Jira toolset, shell code, artifacts | integration-docker | E2E3 matrix | `tests/unit/harness/engines/test_e2e3_tool_matrix.py` (`-m docker`) |
| TGW-17 | edge | Policy decision table `policy_table.yaml` (~200 rows: effect × principal × org rule × taint × data flow × preapproval × grant × credential × budget × caller) with mandatory rows: deny > ask > allow; unclassified → ask/deny; worker principal → deny; `caller=code` = `caller=model`; child outside parent grant → deny; missing credential → unavailable | P/K | table runner | `tests/unit/harness/policy/test_policy_table.py` over `policy_table.yaml` (QA-10; worker principal, child grant and credential rows) |
| TGW-18 | edge | Properties: deny-monotonicity; grant narrowing; taint stickiness across fold, resume, child spawn and code calls; Rule of Two (private ∧ untrusted ∧ external write never allowed without preapproval) | P | hypothesis | `tests/unit/harness/policy/test_policy_properties.py::test_tgw18_*` (QA-10); `harness_core/effects/test_flow.py::test_fl3_*` |
| TGW-19 | failure | Recovery table: effect class × crash position (before intent, after intent, after execute, after outcome) gives the documented outcome; `EffectRecorder` sees ≤ 1 effect per write | U/X | fake SaaS with lost ack | `tests/unit/harness_core/durable/test_qa24_recovery_table.py` (QA-24: 6 classes × 4 positions plus the lost ack); earlier `test_recovery.py`, `test_kill9.py`, `harness_core/test_fake_saas.py` |
| TGW-20 | security | The descriptor coverage check iterates all `@tool`s and MCP templates and fails on an overlay entry naming no tool (stale overlay) | S | — | **partial:** `test_effect_tags_complete.py::test_et2_the_check_lists_an_untagged_tool` (untagged only) |
| TGW-21 | edge | Tool-gateway overhead per call ≤ 5 ms p50 on the endpoint (no network) | N-lat | bench | **new** |
| TGW-22 | security | Tool results above the inline bound go to `/tool-results` and only digests are journaled for code-mode batches (SLO.md finding for P5) | U | — | **new** |

**Fakes and contract suites.** `harness_core/testing/contracts/effect_log.py`, `gateway.py` (endpoint), `interactions.py`; `harness_core/testing/fake_saas.py` with lost-ack and idempotency keys; `tests/unit/harness_core/invoker/test_tool_catalog_contract.py`. `PipesHubEngineAssembler` and `JournalEngineEventSink` have no contract runs (R3 §3).

**Existing tests.** 31 files, 164 tests (unit 149, contract 7, integration-docker 8). Weak tests (R3 §4): `harness_core/gateway/test_endpoint_contract.py:66` sleeps 50 ms then asserts no more progress ticks; `:105-116` sleeps 20 ms then cancels, then sleeps 200 ms before asserting; `harness_core/tools/test_time_bound.py:111-113` asserts `time.monotonic() - started < 3`.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| TGW-G1 | Policy decision table with mandatory rows (TGW-17) | 5 | 3 | 15 | P/K | PR unit | `policy_table.yaml` + runner | none — **closed by QA-10** |
| TGW-G2 | Policy and taint properties incl. Rule of Two (TGW-18) | 5 | 3 | 15 | P | PR unit; nightly | hypothesis | none — **closed by QA-10** |
| TGW-G3 | Replace weak tests: sleeps in `test_endpoint_contract.py:66,105-116`, wall clock in `test_time_bound.py:111-113` (R3 §4) | 3 | 4 | 12 | U | PR unit | wait on events; `ManualClock` | none |
| TGW-G4 | One recovery table over effect class × crash position (TGW-19) | 5 | 2 | 10 | U/X | PR unit; nightly fault | parametrized table with `EffectRecorder` | none — **closed by QA-24** |
| TGW-G5 | Contract runs for `PipesHubEngineAssembler`, `JournalEngineEventSink` (R3 §3) | 3 | 3 | 9 | K | PR unit | subclasses of existing contracts | none |
| TGW-G6 | Stale overlay detection (TGW-20) | 2 | 3 | 6 | S | PR unit | extend `test_effect_tags_complete.py` | none |
| TGW-G7 | Code-mode result size bound (TGW-22) | 3 | 2 | 6 | U | PR unit | batch result test | product change |

## KNW Knowledge read gate and publications

Built, owner SEC. `backend/python/harness_core/knowledge/` (publications, read grant), `backend/python/app/harness/knowledge/`, `backend/python/app/modules/retrieval/`, record access in `app/utils` and the graph provider (`IGraphDBProvider`); Node `application/knowledge-publications.ts`, `transport/publication-routes.ts`.

**Behaviours.**
1. B1 Every knowledge path (search, prefetch, fetch full record, `lookup_record`, raw content, citations) goes through the record-access gate with the real caller, including service-account agents.
2. B2 Access revoked between search and fetch is refused at fetch; an id the run never surfaced is refused before anything is read.
3. B3 No existence oracle: a denied record and a missing record are indistinguishable for every identifier kind (E2E4).
4. B4 Publications (flag): retrieval admits (actor ∩ grant) ∪ publications for the audience; `answer_only` content is never fetched raw; own access wins; revocation stops serving; never another org.
5. B5 Neo4j and ArangoDB decide the same way (backend parity).
6. B6 Batched gate: n ids cost one ACL query; publications read once.
7. B7 Labels name the publication so content is never re-shared outside the audience (P2.5a).

**Levels.** U: gate, publications, fetch. K: `IGraphDBProvider` record-access contract (fake and every adapter), shared record-access fake (C-27). I: real Neo4j and ArangoDB (`tests/integration/graph_db/`). integration-docker: E2E3 knowledge cells. Es: cited answer and cross-user knowledge scenarios.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| KNW-01 | happy | A readable entry is served from the record map | U | `fake_record_graph` | `tests/unit/utils/test_fetch_full_record_access.py::test_fr3_*` |
| KNW-02 | security | An injected entry the reader cannot read is not served; access revoked between search and fetch is refused | U | — | `test_fetch_full_record_access.py::test_fr1_*`, `test_fr2_*` |
| KNW-03 | security | Engine fetch: an id the run never surfaced is refused before anything is read; no internal search → no fetch | U | — | `tests/unit/harness/engines/test_durable_record_fetch.py::test_f2_*` |
| KNW-04 | security | A service-account request keeps the real caller through knowledge tools, prefetch and the durable worker | U | — | `tests/unit/modules/retrieval/test_publication_surfaces.py::test_sf1_*` (R3 §4 flags this file's source-text assertions: replace with behaviour) |
| KNW-05 | happy | Publications: an outsider never gets published records; an audience member does; own access wins; the grant narrows publications | U | — | `tests/unit/modules/retrieval/test_publication_scope.py::test_rs1..rs4` |
| KNW-06 | security | `answer_only` record not fetched; a full publication or own access served; full publication stops at revocation | U | — | `test_fetch_full_record_publications.py::test_md1_*`, `test_md2_*`; `test_fetch_full_record_access.py::test_fr4_*` |
| KNW-07 | security | Publication audience never another org; read decision names the publication | U | — | `harness_core/knowledge/test_publications.py::test_pa1_never_another_org`; `test_read_grant.py` |
| KNW-08 | happy | Published records resolve alike on both backends; ownership read from the graph; a deleted container's publication grants nothing | I | Neo4j + ArangoDB | `tests/integration/graph_db/test_knowledge_publications.py::test_rs10_*`, `test_rt1_*`, `test_rs9_*` |
| KNW-09 | happy | The batch decides what the single check decides | I | real graph | `tests/integration/graph_db/test_record_access_batch.py::test_rb8_*` |
| KNW-10 | edge | n ids cost one ACL query (publications off); a service-account agent one query per principal; publications read once | U | query counter | `tests/unit/harness/test_read_gate_batch.py` |
| KNW-11 | happy | The record-access fake holds the contract; every adapter implements every access method; no test fakes record access by hand | K/S | contract, C-27 | `tests/unit/services/graph_db/test_record_access_contract.py`; `tests/cohesion/test_c27_one_record_access_fake.py` |
| KNW-12 | security | Node: flag off every publication route 404 `publications_disabled`; a body naming whose publication it is is 400 | U | — | `knowledge-publications.test.ts` |
| KNW-13 | security | Labels: a published source keeps its own key; a status for the bare record never passes the published source; a non-audience sender is stubbed in a shared session | U | — | `turn-labels-publications.test.ts` "LB-3"; `tests/unit/services/record_content/test_authorizer_publications.py` |
| KNW-14 | happy | Knowledge tools on the real graph and on ArangoDB from an engine run | integration-docker | E2E3 | `tests/unit/harness/engines/test_e2e3_tool_matrix.py::test_kn_*`, `test_ar_*` |
| KNW-15 | security | No existence oracle for `lookup_record` by record id, external id, short label and URL: denied and missing answer the same, and the search trail does not stop at a denied match (E2E4) | U | — | `tests/unit/harness/knowledge/test_read_gate_matrix.py::test_qa27_1_*` (QA-27: record id, external id, link; fetch, Slack thread and nearby; loop, durable, engine); `test_fetch_record_resolution.py`, `test_lookup_record_id.py` |
| KNW-16 | security | Cross-user e2e: user B asks the agent about a record only user A can read; the answer has no content and no citation of it; on legacy and durable paths | Es | e2e lane | **new** |
| KNW-17 | security | Backend parity matrix: the same ACL fixture (users, groups, teams, KB permissions, publications) decides identically on Neo4j and ArangoDB for every access method | I | both graph DBs | `tests/integration/graph_db/test_knowledge_read_matrix.py::test_qa27_2_*` (QA-27: every surface × path × ownership × publication); `test_knowledge_publications.py::test_rs10_*` |
| KNW-18 | edge | Prefetch and citation rendering for a viewer in a shared conversation use the viewer's access, not the asker's | I | both graph DBs | `test_knowledge_read_matrix.py::test_qa27_5_*` (prefetch of a session's other participant), `test_qa27_4_*` and `test_read_gate_matrix.py::test_qa27_4_*` (shared view per viewer) (QA-27); `shared-citations.test.ts` (Node render) |

**Fakes and contract suites.** `tests/support/fake_record_graph.py` (the one record-access fake, C-27), `tests/support/in_memory_vector_db.py`, the record-access contract in `test_record_access_contract.py`, `tests/harness_replay/knowledge_base.py`.

**Existing tests.** 19 files, 153 tests (unit 100, integration 45, contract 5, cohesion 3). Weak test (R3 §4 risk 7): `tests/unit/modules/retrieval/test_publication_surfaces.py:86-99` asserts caller identity by reading source text — a security property tested by text.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| KNW-G1 | Replace weak test: source-text caller identity in `test_publication_surfaces.py:86-99` with a behavioural test (R3 §4) | 4 | 3 | 12 | U | PR unit | call through the tool with a spy on the gate | none |
| KNW-G2 | Existence-oracle table across identifier kinds (KNW-15) | 5 | 2 | 10 | U | PR unit | parametrized denied-vs-missing equality | none — **closed by QA-27** |
| KNW-G3 | Graph backend ACL parity matrix (KNW-17) | 5 | 2 | 10 | I | backend matrix nightly (Neo4j, ArangoDB) | shared fixture, every access method | none — **closed by QA-27** |
| KNW-G4 | Cross-user knowledge e2e on both paths (KNW-16) | 5 | 2 | 10 | Es | nightly e2e | must-run scenario | QA browser lane |
| KNW-G5 | Viewer-scoped prefetch in shared sessions (KNW-18) | 4 | 2 | 8 | U | PR unit | Python prefetch test | none — **closed by QA-27** |

## ART Artifacts and citations

Built, owner HL. `backend/python/app/harness/artifacts/`, `harness_core/artifacts/`, `backend/python/app/harness/citations.py`; Node `application/shared-artifacts.ts`, `shared-citations.ts`, `projection/artifact-access.ts`, `projection/citation-source.ts`; erasure and fork copies through `enterprise_search` adapters.

**Behaviours.**
1. B1 Artifacts are published through `publish_intent` → `published` → `presented` with lifecycle draft/final/discarded; one card per artifact, updated in place; a discarded draft is never a card.
2. B2 Provenance: a viewer sees an engine artifact only if they can read every source it was built from; no recorded provenance → hidden (C-8).
3. B3 Shared viewers get a placeholder naming nothing for hidden artifacts and denied citations; checks are batched with the viewer's credential and fail closed; the owner is never checked.
4. B4 Citations journal their sources once; survive a crash and reclaim; live citations resolve through an indexed copy or are hidden.
5. B5 Fork (continue privately) copies artifacts by version; erasure removes them; both are retried with the same key.
6. B6 Another org's run or another conversation's artifact is refused.

**Levels.** U: projector, access checks, citations. K: artifacts contract (in-memory), durable citation contracts. cohesion: C-8 provenance. Es: cited answer, artifacts gallery.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| ART-01 | happy | Timeline keeps one view per artifact; draft and final after one outcome are one card; a later presentation updates the card | U | — | `projection/run-projector-artifacts.test.ts` "NP-1", "NP-2", "NP-4" |
| ART-02 | edge | A draft discarded before its outcome is never a card; a legacy presentation (no lifecycle) is the card it always was | U | — | `run-projector-artifacts.test.ts` "NP-3", "NP-5" |
| ART-03 | security | Provenance: every source readable → shown; one unreadable → hidden; no provenance → hidden; input-artifact sources count | S/X | cohesion world | `tests/cohesion/test_c8_artifact_provenance.py::test_c8_1..c8_4` |
| ART-04 | security | Shared artifacts: one batched check with the viewer credential; fails closed; an answer that leaves an artifact out hides it | U | — | `shared-artifacts.test.ts` "AN-3", "AN-4" |
| ART-05 | security | Shared citations: readable and web kept; denied leaves only its number; fails closed; owner never checked | U | — | `shared-citations.test.ts`; `es_controller.shared-citations.test.ts` |
| ART-06 | security | Hidden markers become placeholders; markers without lineage always hidden | U | — | `projection/artifact-access.test.ts` "AN-1", "AN-2" |
| ART-07 | failure | Citations survive a crash and a reclaim on another worker; live citations too | K | durable world | `tests/contracts/test_durable_citations.py::test_ct2_*`; `test_durable_live_citations.py::test_cl3_*` |
| ART-08 | security | Registry: a run of another org is refused; an artifact of another conversation is not editable | U | — | `tests/unit/harness/artifacts/test_registry_store.py` |
| ART-09 | happy | Artifact events pass their schemas; lifecycle optional and closed; markers once per version | U | — | `tests/unit/harness/artifacts/test_events.py` |
| ART-10 | failure | Fork copier: a non-2xx or malformed answer fails the fork (retried with the same key); eraser failure is retried by the job | U | — | `enterprise_search/harness-artifact-copier.test.ts`; `harness-artifact-eraser.test.ts` |
| ART-11 | happy | Engine answer citations: records grouped with what names them; a block label copied verbatim becomes a link | U | — | `tests/unit/harness/engines/test_engine_citations.py` |
| ART-12 | happy | Frontend: an unavailable citation keeps its number and renders "source not available to you"; an unavailable artifact renders a neutral placeholder | U | jsdom | `frontend/app/(main)/chat/components/message-area/response-tabs/citations/__tests__/citation-unavailable.test.ts`; `artifacts-panel-unavailable.test.tsx` |
| ART-13 | happy | Browser: the answer carries the fact from the uploaded document and cites it | Es | Playwright | `frontend/tests/e2e/chat/chat-cited-answer.spec.ts` (runs in `integration-tests.yml` nightly) |
| ART-14 | security | Shared-session e2e: viewer B opens owner A's conversation with an engine artifact built from a record B cannot read; B sees the placeholder in chat, panel and download URL (direct GET 404) | Es | e2e lane | **new** |
| ART-15 | edge | Artifact versions after a workspace restore (WV-2) keep provenance of the restored version | U | — | **new** |
| ART-16 | failure | Presentation stage failure keeps artifacts and citations (R3 M6, FX) | U | — | `tests/unit/harness/test_presentation_stages.py::test_m6_1_*` |

**Fakes and contract suites.** `harness_core/testing/contracts/artifacts.py` (in-memory artifacts contract, `test_memory_artifacts_contract.py`), `adapters/in-memory-artifact-access.ts`, cohesion world `tests/cohesion/support.py`.

**Existing tests.** 24 files, 130 tests (unit 116, contract 5, cohesion 5, e2e 4). No shared-viewer e2e and no direct-download denial check.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| ART-G1 | Shared viewer cannot fetch a hidden artifact by URL (ART-14) | 5 | 2 | 10 | Es/C | nightly e2e; PR unit (route) | Playwright + route test | QA browser lane |
| ART-G2 | Artifact contract over the production registry store (in-memory only today) | 3 | 3 | 9 | K | PR unit | subclass for the production adapter | none |
| ART-G3 | Provenance after workspace restore (ART-15) | 3 | 2 | 6 | U | PR unit | restore then present | none |

## KIT Deterministic test kit

Built, owner QA. `backend/python/harness_core/testing/` (scripted model, `ManualClock`, seeded ids, failpoints, fake SaaS, replay kit, contracts), `backend/python/app/harness/testing_adapters.py`, `backend/python/tests/support/`, Node `backend/nodejs/apps/tests/helpers/` and `tests/support/harness/`, compose fakes `deployment/e2e/fake_llm/server.py`, `fake_saas/server.py`.

**Behaviours.**
1. B1 Every fake that stands in for a port passes that port's contract suite (TEST-STRATEGY principle 2, C-2).
2. B2 The scripted model replays a YAML script identically; script guards turn a harness regression into `ScriptMismatch`; error shapes (429, 500, timeout, malformed, mid-stream cut) are provider-native.
3. B3 `ManualClock` fires due timers in deadline order; a cancelled timer never fires; `sleep` completes only when advanced.
4. B4 Failpoints raise exactly once at the named point; unarmed points are no-ops; `kill9` terminates a child at the point.
5. B5 Fake SaaS: lost ack records the effect and the caller times out; keyed retry does not duplicate; unkeyed retry does.
6. B6 Replay scrubber removes every secret shape and maps emails to stable pseudonyms; recordings replay strictly.
7. B7 Test isolation: registry, bindings and stubs are restored between tests; concurrent binding scopes stay apart.
8. B8 The fake LLM serves Chat Completions, Responses and Messages streaming for the e2e stack, with a control endpoint for call logs, injected latency and mid-stream cuts.

**Levels.** U: the kit's own tests. S/cohesion: C-2 (every port has an in-memory adapter and a contract), C-27 (one record-access fake), C-25 (slow lanes). K: each fake run under its port's contract.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| KIT-01 | happy | A YAML script replays identically across runs; any script is deterministic | P | hypothesis | `harness_core/test_scripted_model.py::TestK1Determinism` |
| KIT-02 | failure | Error shapes; text then error is a mid-stream cut | U | — | `test_scripted_model.py::TestErrors` |
| KIT-03 | happy | `ManualClock`: timers fire in deadline order; cancelled timer does not fire; sleep completes only when advanced | U | — | `harness_core/test_manual_clock.py::TestK5Timers` |
| KIT-04 | happy | Failpoints raise exactly once; unarmed are no-ops; `kill9` SIGKILLs the child at the point | U | — | `harness_core/test_failpoints.py`; `test_failpoint_kill9.py` |
| KIT-05 | happy | Fake SaaS lost ack, keyed and unkeyed retry, key reuse with another payload conflicts | U | — | `harness_core/test_fake_saas.py` |
| KIT-06 | security | Replay scrubber removes each secret shape; URL keeps host and harmless query; pseudonyms stable by key | U | — | `harness_core/test_replay_kit.py::TestScrubber` |
| KIT-07 | happy | Seeded ids: same seed same sequence | U | — | `harness_core/test_seeded_ids.py` |
| KIT-08 | edge | Scoped bindings win over installed and are restored; tasks inherit scope; concurrent scopes stay apart | U | — | `harness_core/test_bindings_scope.py` |
| KIT-09 | happy | A YAML script drives a real agent through tool failure and success | U | — | `tests/unit/harness/test_testing_adapters.py` |
| KIT-10 | security | No test fakes record access by hand; every allowed stub still exists | S | — | `tests/cohesion/test_c27_one_record_access_fake.py` |
| KIT-11 | happy | Root hooks leave a stub made in `before()` in place for later tests (Node isolation guard) | U | — | `backend/nodejs/apps/tests/root-hooks.guard.test.ts` |
| KIT-12 | edge | Every port under `harness_core/*/ports.py` has an in-memory adapter and a contract that runs; C-2 also requires the production adapter to run the same contract (R3 §3: C-2 requires only the in-memory adapter) | S | C-2 | **partial:** `tests/cohesion/test_c2_port_contracts.py` (in-memory only) |
| KIT-13 | happy | Fake LLM server: each wire streams a scripted reply; `/_control` returns the call log; injected latency and mid-stream cut behave as scripted | U | — | **new** (`deployment/e2e/fake_llm/server.py` has no test) |
| KIT-14 | happy | Fake SaaS HTTP server (compose) behaves as the in-process fake for lost ack and idempotency keys | U | — | **new** (`deployment/e2e/fake_saas/server.py` has no test) |
| KIT-15 | edge | Test fakes live outside production packages: `harness_core/journal/memory.py` and Node `adapters/in-memory-*.ts` moved to test support; the import-boundary test forbids production imports of them (R3 M14, WP-2) | S | import boundary | **new** |
| KIT-16 | edge | Shared fixtures: one `clock`, one `_deployment`, one `_pinned` per tree; a lint check flags a new private copy (R3 §4 fixture reuse) | S | — | **new** |
| KIT-17 | failure | A Docker-marked test skips only in its fixture and fails under `PXH_REQUIRE_DOCKER=1` when the image is absent | S | — | **partial:** `harness-docker-lane` job in `.github/workflows/agent-harness-infrastructure.yml` sets it; no unit test of the switch itself |

**Fakes and contract suites.** All of `harness_core/testing/contracts/*.py`; Node `support/*-contract.ts`; `tests/support/{fake_kafka,fake_redis_connection_provider,redis_provider_matrix,fake_record_graph,in_memory_vector_db}.py`.

**Existing tests.** 12 files, 83 tests (unit 73, replay 7, cohesion 3). The compose fakes the e2e lane depends on have no tests of their own.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| KIT-G1 | C-2 requires production adapters to run the contract (KIT-12) | 4 | 3 | 12 | S | PR unit | extend C-2 registry with production adapter paths | none |
| KIT-G2 | Tests for the compose fake LLM and fake SaaS (KIT-13, KIT-14) | 4 | 3 | 12 | U | PR unit | `deployment/e2e/test_fake_llm.py`, `test_fake_saas.py` | none |
| KIT-G3 | Move fakes out of production packages (KIT-15, R3 M14) | 2 | 4 | 8 | S | PR unit | import-boundary test | WP-2 |
| KIT-G4 | Shared fixtures (KIT-16) | 2 | 4 | 8 | S | PR unit | conftest consolidation + check | WP-15 |
| KIT-G5 | `PXH_REQUIRE_DOCKER` switch tested (KIT-17) | 3 | 2 | 6 | S | PR unit | fixture test with image absent | none |

## MCP MCP hub and runners

Built, flags off by default (`MCP_HUB`, `MCP_STDIO_RUNNERS`); owner HL. Covers `backend/python/harness_core/mcp_hub/`,
`backend/python/app/harness/mcp_hub/`, `backend/python/app/harness/mcp_runner/`, `backend/python/harness_core/sandbox/mcp_runner/`,
the legacy client `backend/python/app/agents/mcp/` and the Node routes `backend/nodejs/apps/src/modules/mcp_servers/`.

**Behaviours.**
1. B1 With `MCP_HUB=true`, a discovered tool list is cached per (org, instance, credential owner, instance configuration) and dropped on `tools/list_changed` or a configuration edit.
2. B2 Sessions are pooled per (org, instance, credential owner); one owner's session is never leased to another owner, server or tenant.
3. B3 Only tools whose name, description and schema an admin approved are registered; a changed tool is disabled and the drift recorded once (`mcp_tool_changed`); nothing approved is a load failure (`mcp_tool_unapproved`).
4. B4 An org-configured URL that resolves to a private, loopback, link-local or metadata address is refused (`mcp_url_refused`) before any request; a resolved URL is pinned against DNS rebinding and redirects.
5. B5 Tool names follow the MCP rule and are exported as valid function names; many tools switch on tool search.
6. B6 With `MCP_STDIO_RUNNERS=true`, a STDIO server runs only in a per-owner runner sandbox through the broker; no runner means a load failure with the runner's reason (`runner_unavailable`, `runner_isolation_unavailable`), never a host process.
7. B7 A runner reaches only the hosts its instance declares (its catalog entry's, or a custom server's setup), through the egress proxy; none listed means no network.
8. B8 A server that is down makes its tools unavailable and the run continues (TEST-STRATEGY §2.13).
9. B9 With both flags off, behaviour is today's: per-request sessions, no cache, no approval gate, URLs as configured, manifest routes answer 404.
10. B10 (target, CPD-7/CPD-9) MCP instances become installations; manifest approval moves to the generic `ApprovalWorkflow`; the pin carries the approved manifest digest (R2 F4, CPS).

**Levels.** U for cache, pool, manifest digest, names and runner policy (in-process fake MCP server); K for the schema cache and runner contracts; C for the manifest routes in Python and the Node relay; I (Docker lane) for real runner sandboxes behind the broker and the proxy; X for a run that loads tools from a hub-pooled session across a worker restart; Es for admin approval in the browser then chat use; N-sec for SSRF and cross-owner leakage.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| MCP-01 | happy | Hub on; two sequential requests for one (org, instance, owner) → `tools/list` is called once; the second is served from the cache | U | in-process fake MCP server | `tests/unit/harness/mcp_hub/test_hub_wiring.py::test_sc5_a_second_request_is_served_from_the_cache_and_an_edit_misses` |
| MCP-02 | happy | `tools/list_changed` notification → cached list dropped, next request lists again | U | fake MCP | `test_hub_wiring.py::test_sc6_list_changed_drops_the_cached_list` |
| MCP-03 | happy | Sequential and concurrent leases for one owner → one session | U | `harness_core/mcp_hub` pool | `tests/unit/harness_core/mcp_hub/test_pool.py::test_sp1_one_session_for_sequential_and_concurrent_leases` |
| MCP-04 | security | Owner B leases on the same instance → a different session; A's session is never handed to B | U | pool | `test_pool.py::test_sp2_*`, `test_sp6_a_session_is_never_handed_to_another_owner`; `test_hub_wiring.py::test_sc7_and_sp8_*` |
| MCP-05 | happy | Admin approves the manifest → every listed tool is registered | U | manifest | `tests/unit/harness_core/mcp_hub/test_manifest.py::test_ma2_the_approved_manifest_admits_every_tool` |
| MCP-06 | edge | Server changes one tool's description after approval → only that tool is disabled; drift recorded once | U | manifest + hub | `test_manifest.py::test_ma2_a_changed_description_disables_that_tool_only`; `test_hub_wiring.py::test_ma3_*` |
| MCP-07 | edge | A new tool appears → it waits for approval; a removed tool is listed as removed | U | manifest | `test_manifest.py::test_ma2_a_new_tool_waits_and_a_gone_tool_is_listed_removed` |
| MCP-08 | failure | Approval document unreadable → the instance fails closed (no tool registered) | U | hub | `test_hub_wiring.py::test_ma5_an_unreadable_approval_fails_closed` |
| MCP-09 | security | URL `http://169.254.169.254/` or a name resolving to 10.x → `mcp_url_refused` before any request | U | resolver fake | `tests/unit/harness/mcp_hub/test_ssrf.py::test_sg1_*`, `test_sg2_*`; `test_hub_wiring.py::test_sg5_a_metadata_url_is_refused_before_any_request` |
| MCP-10 | security | DNS rebinding after pinning; redirect to another host → refused, unsent | U | resolver fake | `test_ssrf.py::test_sg3_*`, `test_sg4_*` |
| MCP-11 | security | Transport built for a pinned URL ignores `HTTP(S)_PROXY` | U | — | `test_ssrf.py::test_sg6_the_transport_built_for_a_pinned_url_never_reads_proxy_settings` |
| MCP-12 | failure | A pooled session breaks mid-call → discarded; next call reconnects; the run sees one tool error, not a crash | U | fake MCP crash toggle | `test_hub_wiring.py::test_sp9_*` |
| MCP-13 | failure | Server down at run start → its tools `unavailable`, other tools work, the run completes (B8) | U | fake MCP | **partial**: `test_hub_wiring.py::test_ma4_nothing_approved_is_a_load_failure` covers approval, not a dead server on the durable path |
| MCP-14 | edge | 60 tools on one instance → tool search on by default | U | — | `test_hub_wiring.py::test_ts1_many_mcp_tools_get_tool_search_by_default` |
| MCP-15 | edge | Tool names longer than 64 chars or with dots → valid exported names, unique per run | U | — | `tests/unit/harness_core/mcp_hub/test_names.py`; `tests/unit/harness_engines/test_mcp_tool_names.py` |
| MCP-16 | happy | Runners on; a STDIO catalog instance → spec uses the template's pinned program, not a stored command | U | runner policy | `tests/unit/harness/mcp_runner/test_routing.py::test_mw7_a_catalog_instance_runs_its_templates_program_not_a_stored_one` |
| MCP-17 | security | A custom STDIO instance without the operator opt-in (`MCP_ALLOW_CUSTOM_STDIO`) → refused | U | — | `test_routing.py::test_mw7_a_custom_instance_needs_the_operators_opt_in` |
| MCP-18 | failure | Runners on, broker unreachable → load failure `runner_unavailable` (cause `broker_unreachable`); no host child process | U | fake broker | `tests/unit/harness/test_flag_matrix.py::test_p510_runners_off_is_todays_host_process_and_on_without_a_broker_is_refused` |
| MCP-19 | security | Runner declaring no hosts → no network; with `api.example.com:443` → only that host through the proxy | I (Docker) | real broker + proxy | `tests/unit/harness/sandbox_broker/test_mcp_runner_docker.py`, `test_mcp_hub_runner_docker.py` |
| MCP-20 | security | Runner policy: secrets never in the spec repr; the key digest names the owner; rotation changes the fingerprint | U | runner policy | `tests/unit/harness_core/sandbox/mcp_runner/test_policy.py::test_secrets_never_show_and_fingerprints_tell_rotation_apart` |
| MCP-21 | security | A runner sandbox of owner A is never reused for owner B, including after idle TTL (`MCP_RUNNER_IDLE_TTL_S`) | I (Docker) | real broker | **built** (QA-26): MR-12 in `harness_core/testing/sandbox/mcp_runner.py`, in-memory, fake broker and Docker runc |
| MCP-22 | failure | `MCP_RUNNER_ISOLATION=gvisor` on a broker without `runsc` → `runner_isolation_unavailable`, never weakened to runc | U + gVisor runner | fake broker; real lane: gVisor runner (not in the default CI) | **partial**: refusal by policy in `test_routing.py`; real runsc **new** |
| MCP-23 | happy | Flags off → per-request sessions, no cache, manifest routes 404 (B9) | U, C | — | `test_hub_wiring.py::test_sp8_without_the_hub_every_request_connects`, `test_sg5_without_the_hub_*`; `tests/unit/harness/mcp_hub/test_routes.py` |
| MCP-24 | security | Manifest `PUT` by a non-admin → 403; admin of org B on org A's instance → 404 | C | Node relay + Python route | **built** (QA-26): `tests/unit/harness/mcp_hub/test_qa26_manifest_authz.py` (one table, Python route); Node `tests/modules/mcp_servers/routes/mcp-hub.routes.test.ts` covers the relay |
| MCP-25 | happy | Admin approves in the browser; a chat in quick mode calls the approved tool | Es | fake MCP in e2e stack | **partial**: `frontend/tests/e2e/workspace/u4-manifest-approval.spec.ts` (approval only, dev-only `run.sh`) |
| MCP-26 | security | Run pinned at admission; admin re-approves a changed schema mid-run → the running attempt keeps the pinned digest; drift → `capability_lost` (FR-28, R2 F4) | U, X | CPS digests | `backend/python/tests/unit/harness/assembly/test_descriptor_pins.py` PN-6 (claim-time check) (CPS, merged at `a168c7d5c`); `backend/python/tests/unit/harness/assembly/test_qa3_mcp_reapproval.py` MCP-26a..e (re-approval while an attempt runs), MCP-26d vectors in `descriptor-pins.json` read by `test_descriptor_pins.py` and `descriptor-pins.test.ts` (QA-3) |
| MCP-27 | concurrency | 20 concurrent leases while the pool sweeps idle sessions → no lease gets a closed session | U, P | pool + ManualClock | **built** (QA-26): `tests/unit/harness_core/mcp_hub/test_qa26_lease_vs_sweep.py` |

**Fakes and contract suites.** In-process fake MCP server (`harness_core/testing`), `test_cache_contract.py` (schema cache contract,
in-memory only), `harness_core/sandbox/mcp_runner/test_contract.py` (runner contract; 0 collected tests in the inventory: the body runs
through subclasses), fake broker from `harness_core/testing`. Missing: a Redis-backed or multi-process cache adapter would need the
cache contract; the Node `mcp_servers` relay has no shared contract with the Python manifest route.

**Existing tests.** 39 files / 454 tests: unit 34/442, contract 2/1, integration-docker 2/10, e2e-browser 1/1. Key files:
`test_hub_wiring.py` (hub end to end in process), `test_pool.py` (pooling rules), `test_manifest.py` (digest and drift),
`test_ssrf.py` (URL guard), `test_routing.py` (runner specs), `test_mcp_runner_docker.py` (real runner), `tests/unit/agents/mcp/*`
(legacy client, discovery, OAuth/DCR). No weak tests flagged by R3 here.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| MCP-G1 | Pin does not bind the approved manifest digest; mid-run re-approval or drift is not tested on the durable path (R2 F4, FR-28) | 5 | 4 | 20 | U, X | PR harness lane | MCP-26: pin digest vs live digest, `capability_lost` on drift, both languages from one vector | **closed by CPS (claim-time drift, PN-6) and QA-3 (MCP-26a..e, a re-approval while an attempt runs)** |
| MCP-G2 | No cross-owner reuse test for runner sandboxes across idle TTL and recycle (`MCP_RUNNER_MAX_AGE_S`) | 5 | 2 | 10 | I | Docker lane | MCP-21 with ManualClock-driven sweep and two owners | none |
| MCP-G3 | Dead server on the durable path: tools unavailable, run continues, `unavailable` reason in the effective set (FR-2/FR-3 shape) | 4 | 3 | 12 | U, X | PR harness lane | MCP-13 through the durable driver with the fake MCP crash toggle | none |
| MCP-G4 | Hub flags are off by default; no e2e row runs a chat with `MCP_HUB=true` and an approved manifest | 4 | 3 | 12 | Es | nightly e2e | MCP-25 extended to a chat call; covering-array rows with `tool_family=mcp` | none |
| MCP-G5 | Manifest routes: authz matrix (admin, member, other org) not in one table; Node relay and Python route tested apart | 4 | 2 | 8 | C | PR unit | MCP-24 as a route table test; later folded into C-26 `/control-plane` inventory | CPD-11 |
| MCP-G6 | gVisor runner isolation floor never exercised for real | 4 | 2 | 8 | I | gVisor runner (not in the default CI) | MCP-22 real `runsc` refusal and success | none |
| MCP-G7 | Concurrent lease vs idle sweep race | 3 | 2 | 6 | U, P | PR unit | MCP-27 property with ControlledScheduler | none |
| MCP-G8 | Manifest approval migrated to `ApprovalWorkflow` with no re-approval needed | 4 | 3 | 12 | U, I | PR harness lane | carry-over test: approved manifests stay approved after the CPD-9 migration, revert restores | CPD-9 |

## TSC Toolsets and credentials

Built; owner SEC. Covers `backend/python/app/harness/credentials/`, `backend/python/harness_core/credentials/`,
`backend/python/harness_core/tool_identity/`, `backend/python/app/harness/tool_identity/`, Node `src/modules/toolsets/`.

**Behaviours.**
1. B1 Secrets live only in the vault (`/services/agentHarness/credentials/{orgId}/…`), sealed, versioned; rotation re-encrypts and drops the old key.
2. B2 A run receives credential handles; the secret is resolved at call time and never appears in journal, logs, prompts, spans, LiveBus frames or sandbox FS (TEST-STRATEGY §2.11).
3. B3 Each run resolves the sender's own connection; a shared session or service-account agent never borrows another user's binding (NFR-18).
4. B4 A missing, revoked or refresh-failed credential makes that item `unavailable` and the run continues (FR-2, NFR-35).
5. B5 With `INSTANCE_TOOL_IDS=true`, two instances of one toolset type keep distinct tool ids, names (≤ 51 chars), credentials and approval rules; off keeps today's type-keyed behaviour.
6. B6 The tool-identity migration has dry run, `--apply` and `--revert`, and a type-keyed pin resolves to the migrated instance or is lost, never guessed.
7. B7 A vault store outage fails closed and never reads as an empty vault or provisions a new keyring.
8. B8 Token refresh (toolset, MCP) refreshes before expiry and records failure without exposing tokens.
9. B9 (target, CPD-8) One `BindingStore`, versioned envelope, tombstones beat cached active rows; migration copies, verifies the digest, keeps the old path read-only for one release.

**Levels.** U for vault, sources, handles and identity rules; K for the binding source contract (target); P for name uniqueness under random instance sets; I for vault on real Redis/etcd KV and Mongo agent instances (graph); X for the canary scan across a full scripted run; N-sec for leak scanning.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| TSC-01 | happy | Store a secret → stored bytes are ciphertext; the platform cipher opens it | U | in-memory KV | `tests/unit/harness/credentials/test_vault.py::test_va1_*` |
| TSC-02 | happy | Rotate the keyring → every document re-encrypted, old key dropped, other fields kept | U | — | `test_vault.py::test_va2_*`, `test_va7_*` |
| TSC-03 | failure | KV outage on read → refused (`credential_unavailable`), never an empty vault; on write → keeps history | U | failing KV fake | `test_vault.py::test_va6_*`, `test_fm_g1_*` |
| TSC-04 | security | Durable run with a canary token → journal, logs and prompt bytes carry no canary | U | canary resolver | `tests/unit/harness/credentials/test_leak.py::test_lk1_*` |
| TSC-05 | security | Legacy loop state carries references only | U | — | `test_leak.py::test_lk2_*` |
| TSC-06 | security | Bob's run in Alice's shared session resolves Bob's connection, also on the MCP path | U | — | `tests/unit/harness/test_ss2_credentials.py::test_cr1_*` |
| TSC-07 | security | Service-account agent uses its own connection and no one else's | U | — | `test_ss2_credentials.py::test_cr2_*` |
| TSC-08 | failure | Credential missing for one toolset → that toolset's tools unavailable with `credential_missing`; the rest of the run works (FR-2) | U, X | — | `tests/unit/harness/credentials/test_tool_credentials.py`; durable run continuation and the notice `tests/unit/harness/assembly/test_qa23_credential_availability.py::test_qa23_4_*` (QA23-F1, fixed by FU1) |
| TSC-09 | failure | Credential revoked mid-run → the next call gets `credential_revoked`; the run continues (NFR-35) | U | vault revoke | `test_vault.py::test_va8_a_revoked_document_reads_revoked_*` (store); mid-attempt `test_qa23_credential_availability.py::test_qa23_5_*`, `test_qa23_f3_*` (QA23-F3, fixed by FU1) |
| TSC-10 | happy | Two Jira instances on one agent, flag on → both in the catalog under own ids; each call reaches its own credentials | U | — | `tests/unit/harness/assembly/test_instance_tool_ids.py::test_dr1_*`, `test_lp1_*` |
| TSC-11 | security | Approval rule on instance 1 does not reach instance 2 | U | — | `test_instance_tool_ids.py::test_dr2_*` |
| TSC-12 | edge | One instance with flag on → today's names unchanged | U | — | `test_instance_tool_ids.py::test_one_instance_keeps_todays_names_with_the_flag_on` |
| TSC-13 | edge | Type-keyed pin with two candidate instances → lost (unavailable), never guessed | U | — | `test_instance_tool_ids.py::test_oj3_*` |
| TSC-14 | happy | Migration dry run lists moves; `--apply` moves; `--revert` restores | U | in-memory graph | `tests/unit/harness/tool_identity/test_migration.py`; a crash at every write `test_qa12_migration_crash.py` (QA-12) |
| TSC-15 | happy | Migration against the real graph (Neo4j, ArangoDB) | I | real graph | **partial**: `tests/integration/graph_db/test_agent_toolset_instances.py` (one test, one backend in its lane) |
| TSC-16 | property | For random instance sets, model names are unique per run and ≤ 51 chars | P | — | `tests/unit/harness_core/tool_identity/test_qa23_name_uniqueness.py` (QA-23 property); examples `test_names.py` |
| TSC-17 | happy | Toolset token refresh before expiry; failure recorded, token not logged | U | — | `tests/unit/connectors/test_toolset_token_refresh_service.py`, `test_mcp_token_refresh_service.py`; bindings `test_binding_token_refresher.py`; a failed write-back keeps the rotated tokens `test_qa23_refresh_availability.py` (QA23-F5); two replicas, one renew per binding `test_qa23_refresh_availability.py::test_qa23_3*`, Node `binding-refresher-race.mongo.itest.ts` (QA23-F2, FU1) |
| TSC-18 | security | Node vault interop: a document Node writes opens in Python and the reverse | U | shared vector | `tests/unit/harness/credentials/test_node_vault_interop.py` |
| TSC-19 | security | Logs carry handles, never material, including on error paths | U | caplog | `test_vault.py::test_va5_*`; `tests/unit/harness/test_log_redaction.py` |
| TSC-20 | security | Node toolsets router: every route has `requireScopes`; dead proxy routes 404 (R2 F16) | C | — | **partial:** `backend/nodejs/apps/tests/modules/toolsets/routes/toolsets-route-scopes.test.ts` (CPS, merged at `a168c7d5c`); dead proxy routes not covered |
| TSC-21 | security | Toolset instance list RMW race: two admins add instances at once → both kept (R2 F21) | I | real KV | `tests/unit/api/routes/test_toolset_instances_race.py` (CPD-7, within a process); across replicas `test_qa23_instance_race_replicas.py` (strict xfail QA23-F4) |
| TSC-22 | security | Canary scan of the whole run surface: journal, checkpoints, LiveBus frames, OTel spans, sandbox FS snapshot | X | canary resolver + scanner | **partial**: TSC-04 covers journal/logs/prompt only |
| TSC-23 | happy | (target) `BindingStore` contract: unique per owner, no secret fields, refresh due order; tombstone beats cached active | K | `CP/` binding contract | **new** (CPD-8) |
| TSC-24 | failure | (target) credential migration: copy, verify digest, old path read-only one release; revert restores reads; no secret in migration logs | I | Mongo + KV | CPD-8 `test_binding_migration.py`; QA-12 `test_qa12_credential_migration_crash.py`, `tests/integration/graph_db/test_qa12_credential_owner_orgs.py` (Neo4j and ArangoDB), `backend/nodejs/apps/tests/modules/control-plane/qa12-binder-crash.test.ts` |

**Fakes and contract suites.** `FakeCredentialResolver` with canary tokens (`harness_core/testing`), in-memory KV for the vault,
shared vault interop vector. No shared contract suite exists today for `ToolsetSecretSource`/`McpSecretSource`; the CPD-8
`test_binding_source_contract.py` (docs/control-plane/05 §6.1) is planned.

**Existing tests.** 24 files / 330 tests, almost all unit (23/329), integration 1/1. Key files: `test_vault.py`, `test_leak.py`,
`test_ss2_credentials.py`, `test_instance_tool_ids.py`, `tool_identity/test_migration.py`, `tests/modules/toolsets/controller/toolsets_controller.test.ts`
(139 tests, controller with stubs). No weak test flagged by R3.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| TSC-G1 | Full-surface canary scan (spans, LiveBus, checkpoints, sandbox FS, explain output) over a scripted run touching every tool source | 5 | 3 | 15 | X | PR harness lane | TSC-22 with `kit.assert_no_canary` | none |
| TSC-G2 | Revoked and missing credentials on the durable path: item unavailable, run continues, typed reason reaches the UI (FR-2/FR-3) | 4 | 3 | 12 | U, X | PR harness lane | TSC-08, TSC-09 end to end through the driver | partial: the worker's check closed by FU1 (QA23-F1, QA23-F3 fixed; `x.pipeshub.run.notice` `credential_missing`); open: no notice for an item Node's effective set takes out at admission (CPD-11) or on an engine run |
| TSC-G3 | Toolset instance list RMW race (R2 F21) has no test | 4 | 3 | 12 | I | PR harness lane (KV) | TSC-21 concurrent adds on Redis and etcd | partial: within a process CPD-7; across replicas QA23-F4 |
| TSC-G4 | Node toolsets router authz (R2 F16) | 4 | 3 | 12 | C | PR unit | TSC-20 route table with scopes | **partly closed by CPS (TS-1, TS-2); dead proxy routes open** (`a168c7d5c`) |
| TSC-G5 | Credential migration safety (copy/verify/revert, no secret in logs) | 5 | 2 | 10 | I | PR harness lane | TSC-24 | CPD-8 — **closed**; QA-12 crash sweep |
| TSC-G6 | Binding contract over in-memory and Mongo adapters | 4 | 2 | 8 | K | PR unit + itests | TSC-23 | CPD-8 |
| TSC-G7 | Tool-identity migration only on Neo4j in its lane; ArangoDB untested | 3 | 2 | 6 | I | nightly backend matrix | TSC-15 parametrized over both graph backends | none |
| TSC-G8 | Name uniqueness as a property over random instance sets (TEST-STRATEGY §2.3) | 3 | 2 | 6 | P | PR unit | TSC-16 with Hypothesis | none — **closed by QA-23** |

## SKL Skills

Built (creator scoping landed with CPS, merged at `a168c7d5c`); owner HL. Covers `backend/python/harness_core/skills/`, `backend/python/app/harness/skills/`,
`backend/python/app/agents/agent_loop/skills/`, Node `src/modules/skills/`.

**Behaviours.**
1. B1 A run pins skill bundles by digest at admission; an edit or delete after admission does not reach the run; a legacy pin still resolves to the version it named.
2. B2 Pinned skills are materialised into the sandbox before the engine or `run_code` starts; an unresolvable skill is refused before anything is acquired.
3. B3 A user's unshared custom skills are never loaded for another user's run; an empty `run.skills` means no custom skills (R2 F3, CPS).
4. B4 Candidate approval is limited to the owner or an org admin (R2 F3).
5. B5 Production engine runs receive their pinned skills (`skill_bundles_for` wired; R2 F7, CPS).
6. B6 `ENABLE_SKILLS` (Labs, default on) and `PIPESHUB_ENABLE_SKILLS` gate skill tools identically on both paths.
7. B7 npm skill import parses commands safely and refuses unsafe packages.

**Levels.** U for digests, pins and manager visibility; K for the skills port contract; I (Docker) for materialisation and the skill runtime image; El for skill packs with a live engine; Es for a chat that uses a skill.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| SKL-01 | happy | Skill pinned at admission; author edits content after admission → run sees the pinned version | U | in-memory skills store | `tests/unit/harness/skills/test_pins_end_to_end.py::test_ad4_a_content_edit_and_a_delete_after_admission_do_not_reach_the_run` |
| SKL-02 | edge | Resource edited before the first materialisation → refused, not served | U | — | `test_pins_end_to_end.py::test_ad4_a_resource_edit_before_the_first_materialization_is_refused_not_served` |
| SKL-03 | edge | Legacy pin naming a version → resolves to that version | U | — | `test_pins_end_to_end.py::test_ad4_a_legacy_pin_still_resolves_*` |
| SKL-04 | happy | Digest is stable across key order and changes with content | U | — | `tests/unit/harness_core/skills/test_digest.py` |
| SKL-05 | happy | Engine turn: pinned skills seeded before the engine starts; unresolvable skill refused before acquire | U | fake engine | `tests/unit/harness_core/engines/test_controller.py::test_ct3_*` |
| SKL-06 | happy | Materialiser writes the bundle into a real sandbox | I (Docker) | real broker | `tests/unit/harness/sandbox_broker/test_skill_materializer_docker.py` |
| SKL-07 | security | User A's unshared custom skill is never listed or loaded for user B's run (legacy and durable) | U | SkillManager with two users | `backend/python/tests/unit/agents/agent_loop/skills/test_skill_visibility.py` SK-1..SK-4, SK-11 (CPS, merged at `a168c7d5c`) |
| SKL-08 | security | Agent with no skill assignment → built-ins plus none of the co-workers' skills (empty means none) | U | — | `backend/python/tests/unit/agents/agent_loop/skills/test_skill_visibility.py` SK-1, SK-3 (CPS, merged at `a168c7d5c`) |
| SKL-09 | security | A member who is not owner or admin approves a candidate → 403 | C | Python route | `backend/python/tests/unit/api/routes/test_skill_candidate_approval.py` SK-6, SK-7 (CPS, merged at `a168c7d5c`) |
| SKL-10 | happy | Production `DurableEngineServices` passes `skill_bundles_for`: an engine run with a pinned skill finds it in the sandbox | X | real worker wiring | `backend/python/tests/unit/harness/engines/test_engine_skills_wiring.py` SK-8, SK-9 (wiring) (CPS, merged at `a168c7d5c`); `backend/python/tests/unit/harness/engines/test_e2e3_tool_matrix.py::test_sk_a_pinned_skill_is_in_the_sandbox_as_admitted_and_no_other` (SK-12, three engines, Docker; QA-3) |
| SKL-11 | edge | `ENABLE_SKILLS=false` for the org → skill tools absent on durable and legacy, prompt unchanged | U | Labs fake | **partial**: `tests/unit/services/featureflag/test_platform_settings_skills.py` (flag read only) |
| SKL-12 | happy | Durable run learns a skill (skill learning) and proposes it for review | U | — | `tests/unit/harness/assembly/test_durable_skill_learning.py` |
| SKL-13 | security | npm import refuses shell metacharacters and non-allowlisted registries | U | — | `tests/unit/services/skills/test_npm_command_parser.py`, `test_package_importer.py` |
| SKL-14 | happy | Skill packs install into the engine image; live engine uses a pack | I, El | real engine | `tests/unit/harness/engines/test_real_skill_packs.py`; `test_live_skill_packs.py` (`slow`) |
| SKL-15 | edge | `allowed_tools` in a skill is enforced, not advisory (R2 F3) | U | — | **decided advisory for this release:** `backend/python/tests/unit/harness/assembly/test_qa3_skills.py` holds that the field is parsed, kept and shown and narrows nothing on loop, durable and engine runs (QA-3); enforcement is SKL-G4 |

**Fakes and contract suites.** `test_memory_skills_contract.py` (in-memory skills store under the skills contract; 0 tests
collected directly, subclasses run it), `tests/unit/harness/skills/test_pipeshub_skills_contract.py` (production adapter). Fake
engine from `harness_core/testing`.

**Existing tests.** 22 files / 264 tests: unit 14/246, contract 2/1, integration 1/5 (npm import live), integration-docker 3/5,
eval 1/6, gate-slow 1/1. Most unit tests are the npm parser and importer (127). Pin and visibility coverage is thin.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| SKL-G1 | Cross-user skill leak at run time (R2 F3): no visibility property test on either path | 5 | 4 | 20 | U, P | PR unit | SKL-07, SKL-08: user A's skill never loaded for user B over random assignments; legacy and durable parity | **closed by CPS (SK-1..SK-5, SK-11)** (`a168c7d5c`) |
| SKL-G2 | Engine runs get no skills in production (R2 F7) | 4 | 4 | 16 | X | Docker lane | SKL-10 as a skills cell in `test_e2e3_tool_matrix.py` | **closed by CPS (SK-8, SK-9 wiring) and QA-3 (SK-12, the skills cell)** |
| SKL-G3 | Candidate approval authz (R2 F3) | 4 | 3 | 12 | C | PR unit | SKL-09 | **closed by CPS (SK-6, SK-7)** (`a168c7d5c`) |
| SKL-G4 | `allowed_tools` advisory only | 4 | 3 | 12 | U | PR unit | SKL-15: a skill restricted to two tools cannot call a third | **open: advisory in this release (QA-3 tests it on three paths); enforcement needs the activation design: when a skill is active, how long, entries that name no tool here, engines; after the PR** |
| SKL-G5 | `ENABLE_SKILLS` parity on both paths | 3 | 2 | 6 | U | PR unit | SKL-11 | none |
| SKL-G6 | (target) skills as catalog items with pinned digests replace the in-place CPS fix | 4 | 3 | 12 | U, K | PR harness lane | `SkillsItemRuntime` contract: loads exactly the pinned ids | CPD-7 (QA-3's loop test is in as a strict xfail, `test_qa3_skills.py`) |

## MEM Governed memory

Built, off by default (`/services/agentHarness/memory` unset); owner HL. Covers Node `application/memory-*.ts`, `domain/memory.ts`,
`adapters/mongo/mongo-memory-store.ts`, `transport/memory-*.ts`; Python `harness_core/memories/`, `app/harness/memories.py`.

**Behaviours.**
1. B1 With the policy on (`mode: user|agent`), a durable run is given the actor's accepted, labelled memories (at most 20 pinned, newest first) right before the input; the journal records what was recalled without content.
2. B2 A run may propose memories only in granted scopes; proposals reach Node as `pending` with the run's actor and session.
3. B3 Settlement follows the policy once (`review: auto` accepts; agent scope goes to admin review); an unreadable policy sends every memory to review (fail closed).
4. B4 Settlement is durable: a crash after commit leaves no proposal `pending` forever (R3 M2, fixed by FX via the run sweeper); a replayed commit never discards proposals (R3 M1, fixed by FX).
5. B5 Another user cannot list, edit, review or delete a memory; a shared session keeps personal memories out.
6. B6 Memories are absent on the legacy path and under the kill switch the chips are hidden (R1 F17).
7. B7 Engines are given only memories derived from no record, as instruction text.
8. B8 Content ≤ 500 characters (one constant, R2 F10); logs carry ids and counts, never content.

**Levels.** U for domain rules, recall and render; K for `MemoryStore` (in-memory and Mongo); C for memory routes; I for Mongo store; X for crash between commit and settlement; Es for the memories page and a chat that recalls.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| MEM-01 | happy | Policy `user/auto`; actor has 3 accepted memories → the run journals the recall and the model gets them before the input on every turn | U | in-memory store | `tests/unit/harness_core/memories/test_recall.py::test_py5_*`; `tests/unit/harness/test_memories.py::test_py5_*` |
| MEM-02 | edge | Store unavailable at recall → no memory, run goes on | U | failing store | `test_recall.py::test_py4_an_unavailable_store_gives_no_memory_and_the_run_goes_on` |
| MEM-03 | edge | Resumed attempt → sends the journaled text, asks nothing | U | — | `test_recall.py::test_py4_a_resumed_attempt_sends_the_journaled_text_and_asks_nothing` |
| MEM-04 | happy | `remember` in a granted scope → proposal pending with actor and session | U | — | `test_memories.py::test_py3_*`; `test_recall.py::test_py6_*` |
| MEM-05 | security | Pin naming another user → refused at admission | U | Node domain | `tests/modules/agent-harness/memory-service.test.ts` "AD-3 (forced pin)" |
| MEM-06 | security | Another user lists, edits, reviews or deletes → refused | U, C | — | `memory-service.test.ts` "SV-3"; `memory-routes.test.ts` |
| MEM-07 | failure | Policy unreadable → every memory to review | U | — | `memory-service.test.ts` "SV-7" |
| MEM-08 | concurrency | Two identical terminal commits race → proposals kept, `turnEnded` counted once (R3 M1) | K | execution-store contract | `tests/modules/agent-harness/support/memory-admission-contract.ts` "FX4-1" |
| MEM-09 | failure | Crash between commit and in-process settlement → sweeper settles the pending proposal (R3 M2) | U, X | run sweeper + ManualClock | **partial**: `run-sweeper.test.ts` "FX4-7", `memory-admission-contract.ts` "FX4-2"; no X case with a real process kill |
| MEM-10 | edge | More than 20 accepted memories → newest 20 pinned; 200-item page cap with no paging (R1 F32) | U | — | `tests/modules/agent-harness/memory.test.ts` "at most 20 pinned"; paging **new** |
| MEM-11 | edge | Content of 501 characters → refused with one shared limit constant | U | — | `memory.test.ts` "keeps trimmed text up to 500 characters" (Node only; Python and route copies untested, R2 F10) |
| MEM-12 | happy | Engine gets only record-free memories as instructions; engines keep their own memories off | U | — | `test_memories.py::test_py8_*` |
| MEM-13 | edge | Kill switch on → memory chips hidden and memory access refused for new turns (R1 F17) | U, Es | — | **new** |
| MEM-14 | happy | `MemoryStore` contract on in-memory and Mongo | K, I | `support/memory-store-contract.ts` | `memory-store.test.ts`, `memory-store.mongo.itest.ts` |
| MEM-15 | security | Logs carry ids and counts, never content | U | — | `memory-service.test.ts` "SV-L" |

**Fakes and contract suites.** `tests/modules/agent-harness/support/memory-store-contract.ts` (9 cases; in-memory and Mongo),
`support/memory-admission-contract.ts`, in-memory memory store (in `src/.../adapters/in-memory-memory-store.ts`, R3 M14 says it
should move to test support).

**Existing tests.** 13 files / 77 tests: unit 11/68, contract 1/9, integration 1 (Mongo itest runs the contract). Frontend
`workspace/memories/__tests__/memories.test.tsx` (12).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| MEM-G1 | Crash between commit and settlement on real processes | 4 | 3 | 12 | X | nightly fault | MEM-09 as a crash point in `integration-tests/harness/fault/test_crash_points.py` | none |
| MEM-G2 | Memory under the kill switch (R1 F17) | 3 | 3 | 9 | U, Es | PR unit | MEM-13 | none |
| MEM-G3 | `MAX_MEMORY_CHARS` defined three times (R2 F10) with a test on one copy | 3 | 3 | 9 | U | PR unit | MEM-11 parametrized over Node domain, route and Python; then one constant from `policy-dimensions.json` | CPD-3 |
| MEM-G4 | No e2e row with governed memory on | 3 | 3 | 9 | Es | nightly e2e | covering-array rows with `tool_family=memory` | none |
| MEM-G5 | Memory policy pinned at admission (target): policy loosened mid-run does not widen | 4 | 2 | 8 | U | PR harness lane | pin vs live layer case | CPD-6 |
| MEM-G6 | Memories page paging (R1 F32) | 2 | 3 | 6 | U | PR unit | MEM-10 paging | none |

## APR Approvals, interactions, flow guard and unknown effects

Built (flow guard on by default since UX1); owner SEC. Covers Node `application/interaction-service.ts`, `application/approval-*.ts`,
`application/unknown-effects.ts`, `domain/approvals.ts`; Python `app/harness/approvals.py`, `harness_core/effects/flow.py`,
`harness_core/effects/reconcile.py`, `harness_core/interactions/`, `app/harness/reconcile/`.

**Behaviours.**
1. B1 An external write after the run read private data (any record, or content of unknown provenance such as an MCP or connector result) is held for approval (`HARNESS_FLOW_GUARD_LEVEL=ask`) or refused `tool.denied{rule: flow_external_write}` (`deny`); a read is never asked about; a write with nothing read runs.
2. B2 The flow rule only narrows; the run takes the strictest of the deployment's and every org layer's rule; `HARNESS_FLOW_GUARD=false` leaves only an org's own rule.
3. B3 The org writes policy (`/services/agentHarness/approvals`) asks for writes; absent means allow; unreadable or malformed means ask (fail closed).
4. B4 An interaction is consumed once: concurrent responses produce one decision; the approval executes the original invocation with its args digest; deny never executes; expiry says nothing was sent.
5. B5 Who may decide: the run actor approves; a named approver approves; owner and admin may deny but not approve for the actor; a collaborator neither; other sessions and orgs see not found.
6. B6 "Allow for this conversation" is offered only when the flow rule alone holds the call to a named destination (or for engine writes, UX2); it holds for that tool and destination only, is rebuilt from the journal on a new worker, and a worker-written grant is ignored.
7. B7 An undecided approval suspends once; a decision while no worker holds applies on the next claim; a restart keeps it pending; cancel while holding never runs the call.
8. B8 Unknown effects (crash after intent, lost acknowledgement) are reconciled by a read for hooked tools (Jira create, Slack post) after the R-31 window, never by a write; still unknown parks for an operator; the console and the chat card work whatever `HARNESS_RECONCILE` says.
9. B9 Legacy paths that cannot ask (planExecute, Universal Agent, project chats, kill switch) refuse an external write after a read with `approval_unavailable` (UX3).
10. B10 Child-run approvals are lifted into the parent thread (UX3); scheduled runs' approvals reach the schedule owner.
11. B11 (target, CPD-6) Approvals and the flow rule are read from the pin; the live layer only narrows (R2 F2).
12. B12 Approval cards redact secrets using the shared token-pattern vector (R3 M10, open).

**Levels.** U for the flow rule, policy layers, hold and reconcile kernels; P for deny-monotonicity and strictest-of; K for the interaction store via the execution-store contract; C for approval, decision and unknown-effect routes; X for crash-reconcile and resume across workers; Es for the card flows; N-sec for red-team injection (scripted model attempts the write).

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| APR-01 | happy | Defaults; run fetched a record, then a Slack post → held, card says what, where, which sources | U | scripted model, FakeToolSource | `tests/unit/harness/test_ux1_flow_default.py::test_fg5_with_every_flag_at_its_default_a_post_after_a_read_waits_and_says_why` |
| APR-02 | happy | Nothing read, Slack post → runs without asking | U | — | `test_ux1_flow_default.py::test_fg6_*`; `test_sec1_flow_guard_loop.py::test_fl9_a_post_before_any_read_is_not_narrowed` |
| APR-03 | edge | A read and a post in one batch → judged on what the model had read before the batch | U | — | `tests/unit/harness/test_sec1_flow_guard_loop.py::test_fl9_a_read_and_a_post_in_one_batch_*` |
| APR-04 | edge | Checkpoint restored on a later attempt → the read is still known | U | — | `test_sec1_flow_guard_loop.py::test_fl8_*` |
| APR-05 | security | `HARNESS_FLOW_GUARD_LEVEL=deny` → `tool.denied{rule: flow_external_write}`, model reads an explainable refusal | U | — | `test_sec1_flow_guard_loop.py::test_fl9_deny_*`; `test_ux1_flow_default.py::test_dn1_*` |
| APR-06 | security | Org layer sets deny with the deployment flag off → write refused (loop and engine) | U | — | `tests/unit/harness/test_sec2_flow_per_org.py::test_fo4_*` |
| APR-07 | property | Adding any deny rule or any layer never turns a decision into allow (deny-monotonicity, strictest-of) | P | policy table | `tests/unit/harness/policy/test_policy_properties.py::test_apr07_*` (QA-10); `test_sec2_flow_per_org.py::test_fo2_*` |
| APR-08 | failure | Approvals document unreadable → writes ask (fail closed) | U | failing config | `tests/unit/harness/assembly/test_approval_config.py` |
| APR-09 | happy | Approved within the hold → runs once without suspending; denied within the hold → refused, model goes on | U | ManualClock | `tests/unit/harness_core/durable/test_approval_hold.py::test_kh2_*`, `test_kh3_*` |
| APR-10 | edge | Undecided → suspends once; resolution resumes; no intent journaled before approval | U | — | `test_approval_hold.py::test_kh4_*`, `test_kh5_*` |
| APR-11 | failure | Worker restart while holding → approval stays pending; decision with no worker applies on next claim | U | — | `test_approval_hold.py::test_kh7_*`, `test_kh8_*` |
| APR-12 | edge | Cancel while holding → run ends, call never runs | U | — | `test_approval_hold.py::test_kh9_*` |
| APR-13 | concurrency | 50 concurrent `POST …/interactions/:id/responses` → exactly one decision, others 409 or idempotent replay | K, I | execution-store contract (in-memory, Mongo) | `interactions.test.ts` (single-response rules); 50-way race on Mongo: `qa21-decision-races.mongo.itest.ts` QA21-5 (QA-21) |
| APR-14 | security | Owner denies but cannot approve for the actor; admin denies; collaborator neither; other session not found | U | — | `interactions.test.ts` "NA-1", "NA-2", "NA-4", "NA-5" |
| APR-15 | security | Approver outside the session sees pending approvals with args; admin outside sees no args and may only deny; member sees only own runs; other org nothing | U | — | `tests/modules/agent-harness/approvals-queue.test.ts` "AQ-1".."AQ-9" |
| APR-16 | happy | Allow for this conversation → the same tool and destination run; another destination asks again | U | — | `tests/unit/harness_core/effects/test_ux1_flow_ask.py::test_cg3_*`, `test_cg4_*`; `test_ux1_flow_default.py::test_cg3_*` |
| APR-17 | security | A grant the worker did not offer, or a worker-written grant → refused by the journal / ignored | U | — | `test_ux1_flow_ask.py::test_cg5_a_worker_written_grant_is_ignored`; `test_ux1_flow_default.py::test_cg2_*` |
| APR-18 | edge | No grant offered when the org also asks for writes | U | — | `test_ux1_flow_default.py::test_ar5_no_grant_when_the_org_also_asks_for_writes` |
| APR-19 | failure | Crash after a Jira create's effect → reconciled applied, no re-call; crash before send → re-called once | U, X | reconcile kernel + fake SaaS | `tests/unit/harness_core/durable/test_reconcile_kernel.py::test_kr1_*`, `test_kr2_*`; `tests/unit/harness/reconcile/test_connector_hooks.py::test_cn7_*` |
| APR-20 | failure | Still unknown → parked; operator `applied` journals outcome without executing; `not_applied` re-calls once; `abandon` fails the run | U, C | — | `test_reconcile_kernel.py::test_ko1_*`..`test_ko4_*`; `tests/modules/agent-harness/unknown-effects.http.test.ts` "NO-1".."NO-5" |
| APR-21 | edge | `HARNESS_RECONCILE` unset → console still answers admins; chat card shows the unknown effect | C, U | — | `unknown-effects.http.test.ts` "UX3 UE-7"; `tests/modules/agent-harness/projection/ux3-unknown-effect.test.ts` |
| APR-22 | security | Legacy path (planExecute) after a record read, MCP write → refused `approval_unavailable`; before any read → runs | U | legacy loop | `tests/unit/agents/agent_loop/test_ux3_legacy_flow_guard.py` |
| APR-23 | security | Regenerate of a turn that read private data → same hold on the durable path (R1 F2) | X, Es | — | `tests/unit/harness/test_qa24_regenerate_hold.py` (QA-24: the hold, kernel and session ledger); `tests/modules/agent-harness/ux3-regenerate.mongo.itest.ts` (admission) |
| APR-24 | happy | Deep mode child requests approval → card in the parent thread; decision resumes the child (R1 F3) | U, Es | — | `tests/modules/agent-harness/projection/ux3-child-approvals.test.ts`; browser **new** |
| APR-25 | security | Engine write approval explains itself (`engine_writes`) and accepts a conversation grant never over an org rule | U | — | `tests/unit/harness/engines/test_ux2_engine_reasons.py`; `projection/ux2-engine-reason.test.ts` |
| APR-26 | security | Approval card redacts `github_pat_`, `sk-proj-`, GitLab, Google API key, PipesHub PAT from the shared vector; raw args never rendered without `previewPayload` (R3 M10, R1 F23) | U | `contracts/harness/vectors/token-patterns.json` | **done (QA-11)**: `frontend/app/(main)/chat/__tests__/approval-preview.test.ts`, `.../message-area/__tests__/approval-card-redaction.test.tsx` |
| APR-27 | security | Scripted model attempts exfiltration after reading a planted injection → 0 attacker-bound effects (SLO #14) | N-sec | red-team cases | `tests/unit/harness/security/redteam/test_redteam_verdict.py` (verdict logic); live red-team run blocked (H7) |
| APR-28 | edge | Approval expires (deadline passes) → `approval_expired`, card says nothing was sent | U, Es | ManualClock | **partial**: expiry in domain; browser **new** |
| APR-29 | security | (target) Admin loosens approvals mid-run → running attempt keeps the pinned (stricter) policy; tightening applies within 5 s (NFR-23/24, R2 F2) | U, X | live-layer fake | **new** (CPD-6) |
| APR-30 | happy | Approval-prompt rate < 5% of benign runs (SLO #13) | K | parity cases | `approval_prompts` per parity case; no gate in CI |

**Fakes and contract suites.** `ScriptedModel`, `FakeToolSource` + `EffectRecorder`, `ManualClock`, fake SaaS
(`deployment/e2e/fake_saas/server.py`, Jira and Slack shaped, with lookup endpoints), interaction storage through
`tests/modules/agent-harness/support/execution-store-contract.ts`. Missing: an interactions contract run on Mongo with a 50-way race;
an approval-policy vector shared with the frontend card.

**Existing tests.** 47 files / 354 tests: unit 42/321, component 2/20, e2e-browser 2/5 (dev-only `run.sh`), eval 1/8. Key:
`test_ux1_flow_default.py`, `test_sec1_flow_guard_loop.py`, `test_sec2_flow_per_org.py`, `test_approval_hold.py`,
`test_reconcile_kernel.py`, `test_connector_hooks.py`, `interactions.test.ts`, `approvals-queue.test.ts`,
`unknown-effects.http.test.ts`, `test_ux3_legacy_flow_guard.py`.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| APR-G1 | Live policy reads: approvals and flow rule are read per attempt, so a mid-run loosening widens (R2 F2) | 5 | 4 | 20 | U, X | PR harness lane | APR-29; property `pin ⊓ live ⊑ pin` | CPD-6 |
| APR-G2 | External write without consent across every path is only proven in unit tests; browser card flows run only on the dev script | 5 | 3 | 15 | Es | nightly e2e (CI) | must-run rows: flow guard × {loop, engine, regenerate, child, schedule, legacy} with fake SaaS effect assertions | QA e2e lane in CI |
| APR-G3 | Card redaction drift from the token vector (R3 M10 open) | 5 | 3 | 15 | U | PR unit | APR-26 vitest built from `token-patterns.json` | none |
| APR-G4 | Regenerate with a held write (R1 F2 safety half) | 5 | 2 | 10 | X | PR harness lane | APR-23 | none — **closed by QA-24** |
| APR-G5 | Consume-once race on Mongo | 4 | 3 | 12 | I | PR harness lane (itests) | APR-13 50-way race in `interactions` contract on Mongo | none |
| APR-G6 | Deny-monotonicity and strictest-of as properties (TEST-STRATEGY §2.3) | 4 | 3 | 12 | P | PR unit | APR-07 Hypothesis over layers and rules | none (then CPD-3 vectors) — **closed by QA-10** |
| APR-G7 | Reconcile hooks cover two tools; unknown effects for other writes always park; no test lists which sink tools lack a hook | 4 | 3 | 12 | U | PR unit | inventory test: every sink tool either has a hook or is documented as park-only | none — **closed by QA-24** (`test_qa24_unknown_effects_inventory.py`) |
| APR-G8 | Red-team injection success not gated (live drivers stopped, H7; O-5/O-6 to human pen test) | 5 | 2 | 10 | N-sec | live, release | scripted-attack lane gate = 0 on PR; live red-team per release | none |
| APR-G9 | Expiry seen by the user (`approval_expired`) not in a browser test | 3 | 3 | 9 | Es | nightly e2e | APR-28 with ManualClock-driven deadline on the stack (libfaketime) | none |
| APR-G10 | Approval-prompt rate (SLO #13) measured but not gated | 3 | 2 | 6 | El | release | gate in the release checklist | none |

## BUD Budget ledger

Built; owner HL. Covers Node `application/budget-ledger-service.ts`, `domain/budget-ledger.ts`, `adapters/mongo/mongo-budget-ledger.ts`,
`transport/budget-controller.ts`; Python `harness_core/budget/`, `app/harness/access/budget.py`, `app/harness/access/spend.py`.

**Behaviours.**
1. B1 A root run opens an account from its pin (`maxCostUsd`, `maxTokens`); a child opens with a share only when smaller than the parent's pin.
2. B2 Each model call reserves input plus the call's output limit (P4.17) and settles on reported usage; a response without usage is charged the estimate, never zero; a cut stream is charged its reasoning and text.
3. B3 One idempotency key is one reservation however many ask at once; parallel reservations across a tree never exceed the root or a share.
4. B4 A call the run cannot pay is refused (`budget_exhausted`), the engine run fails with `budget_exhausted`, the loop yields; refusal codes `budget_account_missing|closed`, `budget_reservation_missing|settled`, `budget_share_exceeds_remaining`, `budget_unavailable` are typed.
5. B5 Cache reads and writes are charged once at the provider's price (SEC1, SEC2).
6. B6 `AGENT_HARNESS_BUDGET_LEDGER=false` leaves model calls unmetered (operator escape hatch).
7. B7 (target, CP7) Org and agent budgets and per-call output bounds become policy dimensions; schedule cost bounds unify (1000 in schema vs 5 in domain, R2 F10).

**Levels.** U for tree arithmetic and metering; K for the ledger contract (in-memory, Mongo, Node via HTTP from Python); C for the budget routes; I for Mongo; X for parallel children under one parent; El for cost accounting against provider bills.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| BUD-01 | happy | Root opened from pin; child with a share of its parent | U | — | `tests/modules/agent-harness/budget-ledger.test.ts` "opens the root from its pin and a child with its share" |
| BUD-02 | edge | Child cap only when smaller than parent's pin | U | — | `budget-ledger.test.ts` "childCap"; `tests/unit/harness_core/budget/test_tree.py::test_child_cap_is_a_share_only_when_smaller_than_the_parents_pin` |
| BUD-03 | concurrency | One key, many concurrent reserves → one reservation | K | `support/budget-ledger-contract.ts` | contract "NL-1: one key is one reservation" |
| BUD-04 | concurrency | Parallel reservations across a tree never exceed root or share | K, I | contract on Mongo | contract "NL-2"; `budget-ledger.mongo.itest.ts` |
| BUD-05 | happy | Reservation = input + call limit; run cap refuses a call it cannot pay | U | — | `tests/unit/harness_core/budget/test_output_bound_ledger.py::test_lg3_*` |
| BUD-06 | edge | No usage in response → estimate charged | U | — | `test_output_bound_ledger.py::test_lg2_*` |
| BUD-07 | edge | Repetition-cut stream → reasoning and text charged | U | — | `test_output_bound_ledger.py::test_lg1_*` |
| BUD-08 | security | OpenAI cache writes not double-charged | U | — | `tests/unit/harness_core/budget/test_sec1_cache_charges.py` |
| BUD-09 | failure | Gateway budget refusal mid engine turn → run fails `budget_exhausted` | U | — | `tests/unit/harness_core/engines/durable/test_crash_points.py::test_ek6_*` |
| BUD-10 | failure | Ledger (Node) unreachable from the worker → `budget_unavailable`, call refused, not unmetered | U | Node client fake | **partial**: `tests/unit/harness/access/test_node_budget.py` (client shapes) |
| BUD-11 | edge | Deep run: child shares under the parent's remaining budget; a share above remaining → `CHILD_BUDGET_EXCEEDS_REMAINING` | K | durable deep goldens | `tests/contracts/test_durable_deep_budget.py` |
| BUD-12 | edge | Ledger flag off → no reservation calls, run unmetered, nothing breaks | U | — | **new** |
| BUD-13 | security | Budget routes need the run's gateway token; another run's token → refused | C | — | **partial**: `budget-ledger.http.test.ts` |
| BUD-14 | happy | Schedule firing with a budget above the ceiling → refused, nothing stored | U | — | `tests/modules/agent-harness/schedule-service.test.ts` "BG-3" |
| BUD-15 | edge | `NodeBudgetLedger` runs the full ledger contract, not only `BudgetAccountsContract` (R3 §3) | K | `harness_core/budget` contract | **new** |

**Fakes and contract suites.** `tests/modules/agent-harness/support/budget-ledger-contract.ts` (in-memory and Mongo);
Python `tests/unit/harness_core/budget/test_ledger_contracts.py` (in-memory). `NodeBudgetLedger` runs only the parent
`BudgetAccountsContract` (R3 §3).

**Existing tests.** 19 files / 69 tests: unit 14/53, contract 3/6, component 1/7, integration 1/3. `test_redis_spend.py` is under
`tests/unit` with marker `integration`.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| BUD-G1 | `NodeBudgetLedger` not under the full ledger contract against a live Node | 4 | 3 | 12 | K, X | PR harness lane | BUD-15: Python contract subclass over `HarnessStateClient` to a real Node with Mongo | none |
| BUD-G2 | Ledger unreachable must refuse, not meter zero | 4 | 2 | 8 | U, X | nightly fault | BUD-10 with toxiproxy cutting Node | none |
| BUD-G3 | Org and agent budgets (CP7) | 3 | 3 | 9 | U, K | PR unit | budget meet vectors; ledger contract unchanged | CP7 |
| BUD-G4 | Flag-off path untested | 2 | 2 | 4 | U | PR unit | BUD-12 | none |
| BUD-G5 | Live cost reconciliation against provider-reported usage per release | 3 | 2 | 6 | El | live, release | compare ledger totals with provider-reported usage per model | none |

## SBX Sandbox broker and run_code

Built (runc proven; gVisor and Kata not available); owner PF. Covers `backend/python/app/harness/sandbox/` (`docker_broker.py`),
`backend/python/harness_core/sandbox/` (ports, remote wire, pool, ledger), `backend/python/app/sandbox_broker_main.py`,
`backend/python/app/sandbox/` (run_code executors), `backend/python/app/agent_loop_lib/sandbox/`.

**Behaviours.**
1. B1 Only the broker holds the Docker socket; clients present the worker credential (current or previous in grace); every route is versioned; liveness needs no credential and says nothing else.
2. B2 A create refuses root, any weakening of hardening, images outside the allowlist, and isolation modes the host lacks (gVisor without `runsc` is refused once with a reason; never downgraded).
3. B3 Release leaves no container, volume or network; a create the daemon finished after a client timeout leaves nothing; a broker killed mid-provision leaves an orphan that the reaper removes.
4. B4 A restarted broker adopts running sandboxes; signalling or reading a killed sandbox says it is gone.
5. B5 `DockerProcess.wait()` returns or raises when the stream demux fails (R3 B2, fixed by FX); a copy-out failure keeps the sandbox for the warm sweep (R3 M4, fixed by FX).
6. B6 `run_code` works on legacy and durable paths with execution receipts; parity between them (C-22); no network unless `SANDBOX_ALLOW_NETWORK`, and then public addresses only.
7. B7 A sandbox reaches only the gateways through the egress proxy (C-13) and nothing else from inside (C-28).
8. B8 Erasing a session removes its sandbox folders (`POST /v1/erase-session`, credential and safe ids required).
9. B9 Typed errors cross the wire as themselves; a stale fence is refused over HTTP; a client that disconnects mid-run gets its process killed.
10. B10 Registry and database credentials never appear in argv or environment of legacy sandboxes (SEC2).
11. B11 No blocking I/O on the event loop in executors (R3 M7, fixed by FX).

**Levels.** U for policy, wire, process and pool logic with fake Docker clients; K for the remote broker contract (fake, remote, real Docker); I (Docker lane) for real containers; cohesion C-11/C-12/C-13/C-22/C-28; N-sec for confinement probes; N-lat for start time (SLO #17); isolation runtime cases on gVisor, Kata and K8s runners.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| SBX-01 | happy | Create with the broker shape → hardened container (read-only root, no caps, non-root, pids/memory limits) | I | real Docker | `tests/unit/harness/sandbox_broker/test_docker_broker.py::test_the_container_is_hardened`, `test_the_broker_shape_passes` |
| SBX-02 | security | Each weakening (privileged, root, cap add, host network) → refused | I | — | `test_docker_broker.py::test_each_weakening_is_refused`, `test_root_is_refused` |
| SBX-03 | security | Image outside the allowlist → refused; spellings normalised | I | — | `test_docker_broker.py::test_an_image_outside_the_allowlist_is_refused`, `test_image_spellings_normalise` |
| SBX-04 | edge | gVisor requested without `runsc` → refused once with a reason; runc sets no runtime | I | — | `test_docker_broker.py::test_gvisor_is_not_offered_without_runsc_*`, `test_runc_sets_no_runtime` |
| SBX-05 | happy | Release → no container, volume or network left | I | — | `test_docker_broker.py::test_release_leaves_no_container_or_volume` |
| SBX-06 | failure | Daemon finishes a create after the client timed out → nothing left | I | — | `test_docker_broker.py::test_db14_*` |
| SBX-07 | failure | Broker killed mid-provision → orphan reaped | I | — | `test_docker_broker.py::test_a_broker_killed_mid_provision_leaves_an_orpha…` |
| SBX-08 | failure | Restarted broker adopts the running sandbox and seeds it | I | — | `test_docker_broker.py::test_a_restarted_broker_adopts_the_running_sandbox`, `test_a_restarted_broker_seeds_*` |
| SBX-09 | failure | Copy-out fails on engine < 26 → sandbox kept until the warm sweep saves it (R3 M4) | I | — | `test_docker_broker.py::test_m4_1_a_copy_out_that_fails_keeps_the_sandbox_*` |
| SBX-10 | failure | Demux raises mid-frame → `wait(timeout_s=1)` raises, not hangs (R3 B2) | U | fake reader | `tests/unit/harness/sandbox_broker/test_docker_process.py::test_fx2_1_a_stream_that_breaks_mid_exec_fails_wait_at_once_and_ends_the_output` |
| SBX-11 | security | No credential → 401; wrong credential → refused and nothing created; previous credential accepted in grace, expires after | K, U | remote contract | `tests/unit/harness_core/sandbox/test_remote_broker_contract.py::test_a_request_without_a_credential_is_401`, `test_a_wrong_credential_*`; `test_broker_process.py::test_the_server_accepts_current_and_previous_in_grace`, `test_the_previous_credential_expires_after_its_grace` |
| SBX-12 | happy | Typed errors and refusal reasons cross HTTP; stale fence refused over HTTP | K | remote contract | `test_remote_broker_contract.py::test_every_typed_error_crosses_the_wire_as_itself`, `test_a_stale_fence_is_lease_fenced_over_http` |
| SBX-13 | failure | Client disconnects mid-run → process killed | K | — | `test_remote_broker_contract.py::test_a_client_that_disconnects_mid_run_gets_its_process_killed` |
| SBX-14 | security | `erase-session` requires the credential and safe ids; erases only that session's folders | K, I | — | `test_remote_broker_contract.py::test_erase_session_needs_*`; `test_docker_session_erase.py` |
| SBX-15 | happy | Durable `run_code` with receipts; parity with legacy (C-22) | U, I | — | `tests/unit/harness/assembly/test_durable_run_code.py`; `test_durable_run_code_docker.py`; `tests/cohesion/test_c22_run_code_parity.py` |
| SBX-16 | security | Sandbox reaches only the gateways (C-13); confinement probes from inside find 8098 closed, no metadata, no other containers (C-28) | I | probe script | `tests/cohesion/test_c13_sandbox_reaches_only_gateways.py`; `test_c28_sandbox_confinement.py` |
| SBX-17 | security | `SANDBOX_ALLOW_NETWORK=true` → public only; private, link-local, metadata and egress-bridge addresses firewalled; broker refuses open egress it cannot filter (`egress_firewall_unavailable`) | I | — | `tests/unit/harness/sandbox_broker/test_open_egress_firewall.py` |
| SBX-18 | security | Registry and DB credentials not in argv or env of legacy sandboxes | U | — | `tests/unit/sandbox/test_sec2_registry_credentials.py`, `test_sec2_database_credentials.py` |
| SBX-19 | happy | Workspace survives a sandbox kill (C-11) | I | — | `tests/cohesion/test_c11_workspace_survives_sandbox.py` |
| SBX-20 | failure | No broker with `SANDBOX_MODE=broker` → `SandboxUnavailableError`; sandbox tools dropped, chat still answers | U | — | **partial**: `tests/unit/harness/test_flag_matrix.py`; durable answer path **new** |
| SBX-21 | concurrency | `SANDBOX_MAX_PER_ORG` and `SANDBOX_MAX_TOTAL` reached → typed backpressure, no over-provision under 20 parallel creates | I | real Docker | **built** (QA-26, unit over the legacy governor): `tests/unit/agent_loop_lib/sandbox/test_qa26_quota_parallel_creates.py`; the broker's install ceiling (C2.19): `tests/unit/harness_core/sandbox/test_c2_19_sandbox_cap.py`, real Docker `tests/unit/harness/sandbox_broker/test_docker_broker.py::test_c2_19_the_install_ceiling_counts_every_live_sandbox_and_frees_on_release` |
| SBX-22 | edge | Node run sweeper reaps a sandbox of a terminal run through the broker | U | http reaper fake | `tests/modules/agent-harness/run-sweeper-reap.test.ts` |
| SBX-23 | failure | A run's process budget is its own: work the account starts after the run began (PL-5), or another run holding its whole budget (PL-6), does not spend it; inside confinement's namespace none is nested (PL-8); the run keeps its user id (PL-7). Was the PL-3 load flake (H10) | U | FIFO gate, no timing | `tests/unit/agent_loop_lib/sandbox/test_executor.py::TestProcessBudgetIsTheRunsOwn`, `TestProcessBudgetIsOnTopOfWhatTheUserRuns` (PL-1..PL-4) |
| SBX-24 | security | gVisor (`runsc`) create, exec, put/get, destroy; C-13 and C-28 probes under runsc | I | real runsc | **new**; needs a gVisor runner |
| SBX-25 | security | Kata microVM isolation class; cold start < 5 s, warm pool < 500 ms (SLO #17) | I, N-lat | real Kata | **new**; needs a Kata/KVM runner |
| SBX-26 | security | K8s agent-sandbox provider passes the `SandboxProvider` contract; network policy blocks pod-to-pod | K, I | kind + agent-sandbox controller | **new**; kind cannot prove kernel isolation (K8s lane) |
| SBX-27 | happy | Sandbox start: tier 1 warm < 1 s, cold < 5 s (SLO #17) | N-lat | `bench_harness.py --profile sandbox` | **partial**: `tests/unit/harness/engines/test_real_start_time.py` (`slow`); bench profile not built |
| SBX-28 | happy | Installer and Compose sandbox plane smoke (`SANDBOX_MODE` per profile) | deployment | — | `deployment/docker-compose/tests/sandbox_plane_test.sh`, `sandbox_plane_smoke.sh` |
| SBX-29 | happy | `run_code` output reaches the activity bus chunk by chunk through the broker before the program ends; docker, e2b and local hand it over at completion; the record is journaled with the outcome only when Node accepts `command_activity` | U | scripted process, in-memory bus | `backend/python/tests/unit/harness/assembly/test_ux5_run_code_activity.py` (RC-1..RC-5) |
| SBX-30 | security | Command output and command text redacted through the shared token vector for every entry, at every split offset and any chunking; a private key held to its END line or masked whole; the queue bounded, a live cap per command, a failing bus leaving the record whole | K, P | `CommandActivityContract` (in-memory and Redis buses), Hypothesis | `backend/python/tests/unit/harness_core/activity/test_ux5_redaction.py`, `test_command_activity.py`, `backend/python/tests/unit/harness/test_ux5_activity_bus.py` (RD-1..RD-4, RD-6, CO, BP-1..BP-3) |
| SBX-31 | security | An image labelled `io.pipeshub.engine.candidate` is refused (`candidate_image`) at acquire and at warm creation, whatever its tag; nothing is created; only the lanes' override runs it (EH1) | U, I | fake provider; real Docker | `tests/unit/harness/sandbox_broker/test_candidate_image.py`, `test_candidate_image_docker.py` |
| SBX-32 | security | No production module sets the broker's candidate override (EH1) | U | — | `test_candidate_image.py::test_eh8_*` |
| SBX-33 | happy | The broker reads each pinned engine binary from the image once per image id (never per tag, single flight, failures not kept), vouches for it on every sandbox of that id, cold, warm or adopted, and across the remote wire; an unpinned image is never read (EH2) | U, C | in-memory reader, fake daemon | `tests/unit/harness_core/engines/test_image_binaries.py::test_eh2_1_*`..`test_eh2_6_*`; `tests/unit/harness/sandbox_broker/test_binary_cache.py`; `tests/unit/harness_core/sandbox/test_remote_binaries.py`; `SandboxBrokerContract::test_eh2_13_*`; `ImageBinaryReaderContract` (IBR-1..3, in-memory and Docker) |
| SBX-34 | security | An image id whose binary does not hash to its pin refuses every sandbox on it (`binary_mismatch`, at acquire, warm creation and adoption), never started, one `engine.binary.mismatch` warning per refusal with the image id; a tag moved to other bytes is read again and refused (EH2) | U, I | fake daemon; real Docker | `test_image_binaries.py::test_eh2_2_*`, `test_eh2_3_*`, `test_eh2_4_*`; `test_binary_cache.py::test_eh2_9_*`, `test_eh2_10_*`; `tests/unit/harness/engines/test_engine_binary_cache_docker.py::test_eh2_20_*` |
| SBX-35 | security | The broker vouches for no binary that a mount of the spec (writable, read-only scratch or persistent) or its own `/tmp` covers, at the path or where it resolves (EH2) | U, I | — ; real Docker | `test_image_binaries.py::test_eh2_7_*`; `test_binary_cache.py::test_eh2_8_a_spec_that_mounts_over_*`; `test_engine_binary_cache_docker.py::test_eh2_18_a_mount_over_*` |
| SBX-36 | security | Real images (runc), every engine, the engine spec: overwrite, truncate, append, remove, rename, replace by rename, copy over, chmod, hard and symbolic link over, remount, `put_tar` and `seed_tar` into the binary's folder all fail; the binary still hashes to its pin (EH2) | I | real Docker, real images | `test_engine_binary_cache_docker.py::test_eh2_18_nothing_*` |

**Fakes and contract suites.** `harness_core/testing` fake broker (passes `test_fake_broker_contract.py`), remote broker contract
(`test_remote_broker_contract.py`, 12 cases) run against the fake and, in the Docker lane, `test_remote_docker_contract.py` and
`test_docker_broker_contract.py`; legacy `tests/unit/agent_loop_lib/sandbox/contract/` (`test_backend_contract.py`). Docker lane
fails on skips with `PXH_REQUIRE_DOCKER=1`.

**Existing tests.** 71 files / 915 tests: unit 48/771 (legacy `tests/unit/sandbox` is 400+), contract 3/36, cohesion 5/25,
integration-docker 13/83, deployment 2. PL-3's load dependence was a product bug, fixed by H10 (SBX-23).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| SBX-G1 | gVisor isolation never exercised (probes, contract) | 5 | 2 | 10 | I | gVisor runner (not in the default CI) | SBX-24 | QA isolation workflow |
| SBX-G2 | Kata isolation and SLO #17 tier 2 | 4 | 2 | 8 | I, N-lat | Kata/KVM runner (not in the default CI) | SBX-25 | QA isolation workflow |
| SBX-G3 | K8s agent-sandbox provider contract | 4 | 2 | 8 | K, I | K8s lane | SBX-26 | none |
| SBX-G4 | Quotas under parallel creates | 4 | 3 | 12 | I | Docker lane | SBX-21 | none |
| SBX-G5 | ~~Replace weak test PL-3 (load flake)~~ closed (H10: root cause in the executor, per-run user namespace; SBX-23 deterministic with a FIFO gate) | 3 | 4 | 12 | U | PR unit | SBX-23 | none |
| SBX-G6 | No broker at runtime on the durable path: tools dropped, answer still delivered | 3 | 3 | 9 | U, X | PR harness lane | SBX-20 | none |
| SBX-G7 | Sandbox start bench profile (SLO #17) not built | 3 | 3 | 9 | N-lat | weekly perf | `bench_harness.py --profile sandbox` with `compare.py` gate | none |
| SBX-G8 | 24 h soak: 0 orphan containers (SLO #19) | 4 | 2 | 8 | N-soak | weekly/release | soak with random worker and broker kills, orphan census | none |

## EGR Egress proxy

Built; owner SEC. Covers `backend/python/harness_core/sandbox/egress/` (server, control, tokens, wire), `backend/python/app/harness/sandbox/egress_*.py`,
`backend/python/app/egress_proxy_main.py`, `backend/python/app/agent_loop_lib/sandbox/coding/egress_firewall.py`.

**Behaviours.**
1. B1 A sandbox reaches nothing directly: no route by address, no DNS, no metadata, no other container, no host service, no other sandbox.
2. B2 Through the proxy, a sandbox reaches its token routes (model gateway, tool gateway) with its own placeholder; the proxy injects the real token; the sandbox never holds it.
3. B3 Vendor tokens anywhere in a request (headers, body, URL) are refused; response headers scrubbed; reassembled output never contains the secret.
4. B4 Allowlisted hosts (the hosts an MCP instance declares) pass over plain HTTP; everything else `destination_not_allowed`; private and blocked CIDRs `destination_private_address`.
5. B5 With `EGRESS_CONNECT_TUNNELS=true`, CONNECT to an allowlisted `host:port` with no token route resolves once, checks every address, dials only those, refuses IP literals (`tunnel_ip_literal`), audits open and close; off → `tunnel_refused`.
6. B6 The control plane binds only `EGRESS_CONTROL_HOST`, never a wildcard or the sandbox pool; 8098 and the control port are closed from inside a sandbox (C-28, SEC-12).
7. B7 With `EGRESS_STATE_REQUIRED` (default where the state URL is set), the proxy refuses to start without Redis or `SECRET_KEY`; bindings are sealed in Redis and restored before serving.
8. B8 Each refusal is logged once with correlation ids and no secret, and reported as `gateway.refused` audit (SEC2), deduped and capped.
9. B9 A sandbox that hangs up cancels the upstream call (no spend after interrupt).
10. B10 Remote image URLs and data-carrying links in model output cannot exfiltrate (SEC1).

**Levels.** U for policy, wire, tunnel and control-bind logic; K for egress ports; I (Docker lane) for the real proxy and per-sandbox networks; N-sec for the confinement probes and exfiltration cases; X for proxy restart with live sandboxes.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| EGR-01 | security | Direct internet by address → no route; by name → no DNS, proxy refuses | I | real Docker | `tests/unit/harness/sandbox_broker/test_egress_security_docker.py::test_sec1_*`, `test_sec2_*`, `test_sec9_*` |
| EGR-02 | security | Metadata 169.254.169.254 unreachable directly and through the proxy | I | — | `test_egress_security_docker.py::test_sec3_*` |
| EGR-03 | security | Other containers, host services, another sandbox and its proxy address unreachable | I | — | `test_egress_security_docker.py::test_sec4_*`, `test_sec5_*`, `test_sec6_*` |
| EGR-04 | security | Another sandbox's placeholder or token → refused | I | — | `test_egress_security_docker.py::test_sec7_*` |
| EGR-05 | security | Vendor token in arguments never leaves; the gateway gets the token the sandbox never holds | I | — | `test_egress_security_docker.py::test_sec8_*`, `test_sec11_*` |
| EGR-06 | security | Control plane not reachable from a sandbox | I | — | `test_egress_security_docker.py::test_sec12_*`; `tests/cohesion/test_c28_sandbox_confinement.py` |
| EGR-07 | security | Refusal logged once with correlation ids, no secret | I | caplog | `test_egress_security_docker.py::test_sec13_*` |
| EGR-08 | security | Vendor tokens refused anywhere; look-alikes pass; encoded body cannot be inspected → refused | U | policy | `tests/unit/harness_core/sandbox/egress/test_policy.py::test_vendor_tokens_are_refused_anywhere`, `test_look_alikes_pass`, `test_an_encoded_body_cannot_be_inspected` |
| EGR-09 | security | Two Authorization headers → refused; caller auth dropped only for routes | U | — | `test_policy.py::test_two_authorization_headers_are_refused`, `test_no_token_means_no_authorization_added_*` |
| EGR-10 | security | Non-public and configured cluster ranges refused | U | — | `test_policy.py::test_non_public_is_refused`, `test_a_configured_cluster_range_is_refused` |
| EGR-11 | happy | Allowlisted host passes; not allowlisted → `destination_not_allowed` | U | — | `test_policy.py::test_allowlisted`, `test_not_allowlisted` |
| EGR-12 | edge | Tunnels off → CONNECT refused `tunnel_refused`; plain HTTP unchanged with tunnels on | U | — | `tests/unit/harness_core/sandbox/egress/test_tunnel.py::test_ct1_*`, `test_ct12_*` |
| EGR-13 | security | Tunnel to a host or port off the allowlist, or to a credentialed or gateway destination → refused | U | — | `test_tunnel.py::test_ct3_*`, `test_ct4_*` |
| EGR-14 | security | DNS rebinding: resolved once, rebinding answer never used; every answer must be public | U | — | `test_tunnel.py::test_ct5_*`, `test_ct6_*` |
| EGR-15 | happy | Opaque TLS tunnel to the pinned address, audited once; unbinding closes it | U, I | — | `test_tunnel.py::test_ct8_ct9_*`, `test_ct10_*`; `test_egress_tunnel_docker.py` |
| EGR-16 | security | Control host wildcard, unset or sandbox-facing → refused at start | U | — | `tests/unit/harness_core/sandbox/egress/test_control_bind.py::test_ec1_*`, `test_ec2_*` |
| EGR-17 | failure | State required and Redis down at start → refuses to start (`egress_state_unavailable`) | U | — | `tests/unit/harness/test_egress_state.py::test_h1_ep1_ep2_*` |
| EGR-18 | failure | Proxy restart with live sandboxes → bindings restored before serving; sandboxes keep working | U, I | — | `test_egress_state.py::test_pr3_the_role_restores_its_bindings_before_serving`; `test_egress_durable_docker.py` |
| EGR-19 | failure | A sandbox hangs up mid-call → upstream call cancelled | U | — | `tests/unit/harness_core/sandbox/egress/test_server.py::test_ps7_*` |
| EGR-20 | security | Model-output exfiltration: remote image URL, data-carrying link → removed or refused | U | — | `tests/unit/harness_core/gateway/test_sec1_model_exfiltration.py` |
| EGR-21 | security | Refusals become `gateway.refused` audit events, deduped and capped, shown as `effectivePolicy.blockedRequests` | U | — | `tests/unit/harness_core/sandbox/egress/test_sec2_refusal_reports.py` |
| EGR-22 | security | Secret in URL query never in proxy logs | U | caplog | **partial**: `test_server.py` (around :210-221) asserts after `await asyncio.sleep(0.2)` (sleep-then-assert, R3 risk 3) |
| EGR-23 | security | SSRF resolver refuses private, loopback, metadata, NAT64-mapped addresses | U | resolver | **partial**: `test_egress_state.py:270-274` `test_pr4_the_ssrf_policy_refuses_*` resolves for real and asserts `< 10 s` wall time (R3 risk 4: real DNS, wall clock) |
| EGR-24 | security | gVisor sandboxes behind the proxy: same SEC-1..SEC-13 probes under runsc | I | — | **new**; needs a gVisor runner |
| EGR-25 | security | Tunnelled traffic: credential-shape scan no longer covers it; document and test that only allowlisted hosts are reachable (P5.4b §1.2) | I | — | `test_egress_tunnel_docker.py` (partial; residual risk accepted) |

**Fakes and contract suites.** Egress ports contract `tests/unit/harness_core/sandbox/egress/test_port_contracts.py` (0 tests collected
directly; subclasses), fake resolver, fake upstreams, real proxy in the Docker lane.

**Existing tests.** 18 files / 152 tests: unit 12/117, contract 1/0, integration-docker 5/35. Weak tests:
`tests/unit/harness_core/sandbox/egress/test_server.py:219` (sleep 0.2 s then assert no log line),
`tests/unit/harness/test_egress_state.py:274` (real DNS, wall-clock bound).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| EGR-G1 | Replace weak test: real DNS and wall-clock bound in `test_egress_state.py:274` | 3 | 4 | 12 | U | PR unit | EGR-23 with an injected resolver and ManualClock | none |
| EGR-G2 | Replace weak test: sleep-then-assert in `test_server.py:219` | 3 | 4 | 12 | U | PR unit | EGR-22 waits on the response-logged event | none |
| EGR-G3 | Confinement probes under gVisor | 5 | 2 | 10 | I | gVisor runner (not in the default CI) | EGR-24 | QA isolation workflow |
| EGR-G4 | Egress in the e2e matrix: `broker_allowlist` and `broker_open` rows with probes from the browser-driven run | 4 | 2 | 8 | Es | nightly e2e | covering-array sandbox factor with `deployment/e2e/check_egress.py` after each row | none |
| EGR-G5 | Proxy failover under load (restart while 20 sandboxes stream) | 3 | 2 | 6 | X | nightly fault | toxiproxy + `docker kill` on the proxy | none |

## WSP Workspace store, snapshots and warm pool

Built (warm pool and snapshots off by default); owner PF. Covers `backend/python/harness_core/workspace/` (forks, sealing, sync),
`backend/python/app/harness/workspace/`, Node `application/workspace-*.ts`, `domain/workspace*.ts`, `adapters/mongo/mongo-workspace-store.ts`,
`adapters/mongo/workspace-head-cas.ts`, `backend/python/harness_core/sandbox/pool.py`.

**Behaviours.**
1. B1 Workspace HEAD moves only by CAS on revision; of two concurrent commits exactly one wins; reads are session-scoped.
2. B2 Objects are sealed per partition with the session key; dedupe is per partition; a ref does not open in another partition; destroying the key shreds every object.
3. B3 A child writes only its fork; the parent merges each joined child before the next wave (`keep_target`); a merge key answers the recorded merge; overlaps are reported.
4. B4 One writer per workspace (`WorkspaceWriters` lease): a second writer gets `workspace_writer_busy`; an ended run's lease is taken over before expiry; the engine ticker survives store errors and stops as writer-lost before the lease lapses (R3 B3, fixed by FX).
5. B5 Sync into and out of sandboxes refuses unsafe archive members (links out of root, devices, set-id bits, too many entries, oversized), skips what the store cannot hold.
6. B6 Snapshots (`HARNESS_ENGINE_SNAPSHOTS`): sealed with the session key; survive a sandbox kill; another session never reads them; a secret planted in HOME refuses the snapshot; erasure makes them unreadable; over cap refused.
7. B7 Warm pool (`SANDBOX_WARM_POOL_MAX`): shapes are run-free templates; a handed-out sandbox never re-enters the pool; at most the ceiling, holding at most min(memory × 0.25, memory − held memory), each sandbox counted by its own memory.
8. B8 An unreadable parent manifest fails the save (R3 M5, fixed by FX).

**Levels.** U and P for revisions, forks, sealing and archive safety; K for the workspace repository (Node in-memory and Mongo) and the Python sync and workspace contracts; C for the workspace routes; I for Mongo and Docker; X for crash during merge and writer takeover across workers.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| WSP-01 | happy | Commit, read back, file list sealed at rest | U | in-memory | `tests/modules/agent-harness/workspace-service.test.ts` "commits, reads back and seals" |
| WSP-02 | concurrency | Two concurrent commits on one HEAD → exactly one wins | K, I | `support/workspace-repository-contract.ts` | contract + `workspace-store.mongo.itest.ts` |
| WSP-03 | security | Workspace of another session → refused and logged; an attempt that no longer owns the run cannot write | U | — | `workspace-service.test.ts` "NW-2", "NW-3" |
| WSP-04 | security | Stored bytes are ciphertext; tampered bytes or forged hash fail authentication | U | — | `tests/unit/harness_core/workspace/test_sealing.py::test_sk1_*`, `test_sk7_*` |
| WSP-05 | security | A ref does not open in another partition; destroying the key shreds every object; a reissued key cannot open shredded bytes | U | — | `test_sealing.py::test_sk3_*`, `test_sk4_*` |
| WSP-06 | happy | Fork shares origin objects (no copy); merge disjoint changes; report overlaps; roll back | U | — | `tests/unit/harness_core/workspace/test_forks.py::test_wf9_*`, `test_merge_table`; `workspace-service.test.ts` "forks, merges disjoint changes" |
| WSP-07 | edge | Same merge key → recorded merge, nothing written | U | — | `workspace-service.test.ts` "NF-1: the same merge key" |
| WSP-08 | concurrency | Two concurrent retries of one fork → same fork | U | — | `workspace-service.test.ts` "two concurrent retries of one fork" |
| WSP-09 | concurrency | Second writer → `workspace_writer_busy`; ended run's lease taken over before expiry | U | — | `workspace-service.test.ts` "NF-2" |
| WSP-10 | failure | Engine ticker: autosave raises once → renewal continues, wall limit still stops the turn (R3 B3) | U | ManualClock | `tests/unit/harness_core/engines/test_controller.py::test_fx3_3_a_failed_autosave_is_logged_and_the_wall_limit_still_stops_the_turn` |
| WSP-11 | failure | Renew fails with a non-busy error until the lease budget is exhausted → writer lost, no further saves | U | ManualClock | **partial**: FX tests in `test_controller.py`; X case with a second writer taking over **new** |
| WSP-12 | property | For random edit sequences, reachable objects ⊆ stored; HEAD moves only by CAS; forks O(1) (TEST-STRATEGY §2.6) | P | in-memory | `tests/unit/harness_core/workspace/test_workspace_revision_machine.py` (QA-13); `test_fork_properties.py`, `sync/test_roundtrip_property.py` |
| WSP-13 | security | Archive with link out of root, device, set-id bit → skipped or refused; too many entries refused before upload | U | — | `tests/unit/harness_core/workspace/sync/test_archive_safety.py::test_tp1_*`..`test_tb4_*` |
| WSP-14 | happy | Sync round trip into a real sandbox and back | I | real Docker | `tests/unit/harness/sandbox_broker/test_workspace_sync_docker.py` (0 collected directly: verify it runs) |
| WSP-15 | happy | Snapshot survives a sandbox kill; dead one gives none | I | — | `tests/unit/harness/sandbox_broker/test_docker_snapshots.py::test_dk1_*` |
| WSP-16 | security | Another session never reads the snapshot; secret in HOME refuses the snapshot; erasure makes snapshots unreadable | I | — | `test_docker_snapshots.py::test_dk3_*`, `test_dk4_*`, `test_dk5_*` |
| WSP-17 | edge | Snapshot over its cap → refused | I | — | `test_docker_snapshots.py::test_dk6_*` |
| WSP-18 | happy | Warm pool: two runs share a shape; persistent mounts never warm; handed-out sandbox never re-enters | U | — | `tests/unit/harness_core/sandbox/test_warm_pool.py::test_ws1_*`, `test_ws5_*` |
| WSP-19 | security | A warm sandbox handed to org B never carried org A's scratch (shape is run-free) | I | real broker | **built** (QA-26): WP-11 in `harness_core/testing/sandbox/pool_contract.py`, fake broker and `test_docker_warm_pool.py` |
| WSP-20 | failure | Unreadable parent manifest → save fails, not a full-tree diff (R3 M5) | U | — | `tests/unit/harness_core/engines/durable/test_fold_and_sink.py::test_m5_1_a_parent_the_store_cannot_read_fails_the_save_*` |
| WSP-21 | failure | Crash during merge of a joined child → merge resumes by key once; no lost or doubled change | X | failpoint | **new** |
| WSP-22 | edge | Workspace over the fork limit → `WORKSPACE_TOO_LARGE`, nothing written | K | session-fork contract | `support/session-fork-contract.ts` "FK-14" |
| WSP-23 | happy | Workspace routes: auth, 404 for another org, sizes | C | — | `tests/modules/agent-harness/workspace.http.test.ts` |
| WSP-24 | happy | Snapshot restore cost and engine start with snapshots (P5.9b) | N-lat | — | `tests/unit/harness/engines/test_real_snapshot_cost.py` (`slow`) |

**Fakes and contract suites.** Node `support/workspace-repository-contract.ts` (in-memory and Mongo); Python
`tests/unit/harness_core/workspace/test_memory_workspace_contract.py`, `test_node_workspace_contract.py` (Node-backed adapter),
`sync/test_sync_contract_fake.py`; sealing vectors (`test_sealing_vectors.py`, shared with Node `workspace-sealing.test.ts`).

**Existing tests.** 32 files / 175 tests: unit 23/136, contract 4/17, component 1/5, integration 1/5, integration-docker 3/12.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| WSP-G1 | Writer takeover across two real workers: the old writer stops saving before the new one writes (R3 B3 end to end) | 5 | 2 | 10 | X | nightly fault | WSP-11 with SIGSTOP on the first worker past the lease | none |
| WSP-G2 | Crash during merge | 4 | 2 | 8 | X | nightly fault | WSP-21 failpoint `workspace.merge.after_commit` | none |
| WSP-G3 | Stateful revision machine (GC never deletes reachable data) | 5 | 2 | 10 | P | nightly (5,000 examples) | WSP-12 `WorkspaceRevisionMachine` | none — **closed by QA-13** |
| WSP-G4 | Warm-pool cross-org reuse | 5 | 2 | 10 | I | Docker lane | WSP-19 | none |
| WSP-G5 | `test_workspace_sync_docker.py` collects 0 direct tests: confirm the parametrized body runs in the Docker lane | 3 | 2 | 6 | I | Docker lane | assert collected count in the lane summary | none |

## ENG Engines and engine controller

Built (opencode, Codex and Claude Code 2.1.294 released; Claude Code admitted by CC1); owner HL. Covers `backend/python/harness_core/engines/`
(controller, durable kernel, profiles), `backend/python/harness_engines/` (opencode, codex, claude_code adapters, allowlist, release),
`backend/python/app/harness/engines/`, Node `domain/engine-profiles.ts`, `ports/engine-profiles.ts`.

**Behaviours.**
1. B1 An engine agent's durable run is admitted only when `HARNESS_ENGINE_RUNS` is on, the broker exists, the org ceiling admits a released engine (opencode, then Codex) and the model gateway serves the engine's wire; otherwise queued, refused or failed with the FLAGS.md reason.
2. B2 The controller prepares the sandbox (workspace head, pinned skills) before the engine starts; events reach the sink in order with an attempt-independent turn id; outputs are drafts during the turn and final on success; deliverables refuse links and files outside `out/`.
3. B3 Autosave every 20 file changes, every two minutes (injected clock), before a gateway tool call and at turn end; limits (wall, steps) stop the turn.
4. B4 A worker killed at each boundary resumes without duplicate effects; killed twice, the third attempt completes; a dead engine is a lost turn with a typed reason; an engine dead in every attempt is quarantined, never a kernel error.
5. B5 Effects from an engine: non-idempotent effect without outcome becomes unknown and never runs again; idempotent runs once more; approvals hold, deny never runs.
6. B6 Engine profile narrowing: agent profile ≤ org ceiling ≤ release profile; a widening is refused `ENGINE_PROFILE_WIDENING`; a loop parent's engine children include the agent layer (R2 F5, CPS).
7. B7 Engine config is root-owned and read-only inside the sandbox (SEC1); the engine reaches only gateways (C-13).
8. B8 Version pins: an engine version off the allowlist is refused; images carry the expected digests; licences pass the scan.
9. B9 Engine answers carry citations; full-record fetch works; a lost pinned tool does not crash-loop (E2E3 F2–F4).
10. B10 Cancel interrupts, saves, discards drafts and releases.

**Levels.** U with the fake engine and fake broker for controller and kernel; K for engine ports and fake-engine conformance; I (Docker lane) for real binaries (opencode, Codex; Claude Code when admitted); X for the crash matrix (G4, P9 fault matrix, nightly); El for the live matrix (E2E4) under a registered plan; Es for the engine card in the browser.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| ENG-01 | happy | Fake engine turn: spec and outcome; head in sandbox before start without engine config | U | fake engine, fake broker | `tests/unit/harness_core/engines/test_controller.py::test_ct1_*`, `test_ct2_*` |
| ENG-02 | happy | Events reach the sink in order with turn id | U | — | `test_controller.py::test_ct4_*` |
| ENG-03 | happy | Autosave at 20 changes, before a gateway call, after 2 min on ManualClock, at turn end | U | ManualClock | `test_controller.py::test_ct5_*` |
| ENG-04 | security | Deliverables refuse symlinks and files outside `out/`; each refused output recorded once | U | — | `test_controller.py::test_ct6_*`, `test_ro_1_3_*`, `test_ro_4_6_*` |
| ENG-05 | happy | Checkpoint in session scope; next turn restores; unusable state resumes from workspace, flagged | U | — | `test_controller.py::test_ct7_*`..`test_ct9_*` |
| ENG-06 | edge | Cancel interrupts, saves, discards drafts, releases | U | — | `test_controller.py::test_ct10_*` |
| ENG-07 | edge | Wall and step limits stop the turn; version off allowlist refused | U | — | `test_controller.py::test_ct11_*`, `test_ct12_*` |
| ENG-08 | failure | Worker killed at each boundary → resumes without duplicates; killed twice → third completes | U | failpoints | `tests/unit/harness_core/engines/durable/test_crash_points.py::test_ek14_*` |
| ENG-09 | failure | Engine vanished, sandbox gone under a gateway call, broker gone → lost turn with typed reason; dead every attempt → quarantine | U | — | `test_crash_points.py::test_ek15_*`, `test_p9_fb3_*`, `test_h1_eg3_*` |
| ENG-10 | failure | Lost lease mid-turn → commits nothing more | U | — | `test_crash_points.py::test_ek19_*` |
| ENG-11 | failure | Non-idempotent effect without outcome → unknown, never runs again; idempotent runs once more | U | — | `tests/unit/harness_core/engines/durable/test_effects_and_approvals.py::test_ek9_*` |
| ENG-12 | security | Approval decided within hold runs once; denied never runs; re-call after denial refused | U | — | `test_effects_and_approvals.py::test_ek10_*` |
| ENG-13 | happy | Real opencode and Codex: conformance, controller, durable run, tools | I | real binaries | `tests/unit/harness/engines/test_real_conformance.py`, `test_real_codex_conformance.py`, `test_real_controller.py`, `test_real_codex_controller.py`, `test_real_durable.py`, `test_real_tools.py` |
| ENG-14 | happy | Tool matrix: knowledge (Neo4j, ArangoDB), MCP through a runner, connector read and write, knowledge + code + artifact | I | real components | `tests/unit/harness/engines/test_e2e3_tool_matrix.py` |
| ENG-15 | failure | Connector write journaled once across a crash and redrive | I | — | `test_e2e3_tool_matrix.py::test_cn4_*` |
| ENG-16 | failure | Crash matrix on real engines (G4) and P9 fault matrix | X | real Docker | `test_g4_crash_matrix.py`, `test_p9_fault_matrix.py` (`slow`); the crash nightly is maintained outside this repository |
| ENG-17 | security | Engine config root-owned and read-only; agent cannot rewrite it in-turn | I | — | `tests/unit/harness/engines/test_sec1_engine_config_docker.py` |
| ENG-18 | security | Engine control credential and history unreachable from the agent's shell (SEC2 O-5, O-6) | N-sec | — | **new**; deferred to the human pen test |
| ENG-19 | security | Profile narrowing: agent ⊑ org ⊑ release; widening refused `ENGINE_PROFILE_WIDENING` | U | profile vectors | `tests/unit/harness_core/engines/test_profile_narrowing.py`, `test_profile_vectors.py`; Node `engine-profiles.test.ts` |
| ENG-20 | security | Loop parent spawns an engine child → child authority includes the agent's own `engineProfile` layer (R2 F5) | U | — | `backend/nodejs/apps/tests/modules/agent-harness/child-runs-engine-authority.test.ts` NO-6 (CPS, merged at `a168c7d5c`) |
| ENG-21 | edge | `reasoningEffort` above the profile ceiling → refused or clamped; `minimal` enum consistent (R2 F5, F17) | U | — | **partial:** `backend/nodejs/apps/tests/modules/agent-harness/narrow-only.test.ts` NO-1..NO-3 (CPS, merged at `a168c7d5c`); QA-2 QA2-7, QA2-8 (`qa2-profile-ladders.test.ts`, `test_qa2_profile_ladders.py`: the ladders and the requested-effort table in both languages; `minimal` decided D2) |
| ENG-22 | failure | `HARNESS_ENGINE_RUNS` off → run waits for an `executor_engine` worker; broker absent → `broker_unavailable`; wire unserved → `model_api_unavailable` | U | — | **partial**: `tests/modules/agent-harness/engine-availability.test.ts`; claim deadline for an engine run waiting forever **new** |
| ENG-23 | happy | Claude Code adapter: launch, map, state, stream units; real conformance on the pinned binary | U, I | — | `tests/unit/harness_engines/claude_code/*`; `test_real_claude_code_conformance.py` (Docker lane; 26 pass on 2.1.294, QA-31) |
| ENG-24 | happy | Live matrix (k = 3, 2/3 per cell, invariants) for opencode and Codex | El | registered plan | `tests/unit/harness/engines/test_e2e4_live_matrix.py` (`slow`) |
| ENG-25 | happy | Engine card in the browser: progress, approval, answer with citations, restore on reload (R1 F11) | Es | e2e stack | `frontend/tests/e2e/chat/chat-durable-engine-e2e.spec.ts` (dev-only `run.sh durable`) |
| ENG-26 | security | Images and licences: digests pinned, licence scan passes | U | — | `tests/unit/harness_engines/test_engine_image.py`, `test_licence_scan.py` |
| ENG-27 | edge | Engine instructions: assert the built prompt's behaviour (sections present for the pinned profile), not source text | U | — | **partial**: `tests/unit/harness/engines/test_engine_instructions.py:28` asserts on source text (R3 risk 7) |
| ENG-28 | N-lat | Engine start to first event within budget; start not delayed 5–10 s by inline admission (R1 F11) | N-lat | — | **partial**: `test_real_start_time.py` (`slow`); LAT1 notify path **new** |
| ENG-29 | happy | An engine's shell commands (Codex deltas live; Claude Code and opencode at completion) on the activity bus and journaled as records; progress lines redacted (they were raw) | U | presenter + journal sink | `backend/python/tests/unit/harness/engines/test_ux5_engine_activity.py` (EG-1, EG-2, RD-5) |
| ENG-30 | happy | Engine plans (Codex `turn/plan/updated`, Claude Code `TodoWrite`, opencode `todowrite`) become core `task.created`/`task.updated`, one per changed item | U | mapper fixtures | `backend/python/tests/unit/harness_engines/test_ux5_plan_events.py`, `test_ux5_engine_activity.py` (EG-3) |
| ENG-31 | security | The shared binary check: only an exact `sha256sum` match passes; another digest, a missing binary or malformed output → `binary_mismatch` naming kind, version and both digests; a hash killed by a signal → `engine_crashed`; another reported version → `binary_mismatch` (EH1) | U | fake sandbox | `tests/unit/harness_core/engines/test_binary.py` |
| ENG-32 | security | opencode, Codex and Claude Code each refuse a binary that does not hash to its pin before anything runs (only `sha256sum` ran); the version check still holds after a matching hash (EH1) | U | fake sandbox | `tests/unit/harness_engines/test_engine_binary.py::test_eh2_*`, `test_eh5_*`; `opencode/test_driver.py::test_oc12_*`; `claude_code/test_adapter.py::test_ca2_*` |
| ENG-33 | security | Each adapter launches the absolute path it hashed (opencode's import and export too), and each image's `Dockerfile` puts the binary there (EH1) | U | — | `test_engine_binary.py::test_eh3_*`, `test_eh4_*` |
| ENG-34 | failure | A durable run whose session is refused `binary_mismatch` fails once (no retry, no quarantine), releases its sandbox, and the admin's warning carries the run id, engine kind and pinned version (EH1) | U | fake engine, fake broker | `tests/unit/harness_core/engines/durable/test_kernel.py::test_eh10_*` |
| ENG-35 | happy | `binary_mismatch`, `candidate_image`, `engine_unavailable`, `engine_image_missing` read as "the engine can't run here; ask your admin" in every locale, on the reply and the task card; Node's saved failure says the same (EH1) | U | — | `frontend/app/(main)/chat/__tests__/eh1-engine-unavailable.test.ts`; `backend/nodejs/apps/tests/modules/agent-harness/projection/eh1-engine-unavailable.test.ts` |
| ENG-36 | security | Real images (runc): a retagged image whose binary is a wrapper that reports the pinned version is refused by each real adapter before the wrapper runs (EH1) | I | real Docker, real images | `tests/unit/harness/engines/test_engine_binary_docker.py` |
| ENG-37 | happy | An adapter whose sandbox vouches for its binary with the pin runs no `sha256sum` and still checks the reported version; another vouched digest is `binary_mismatch` before anything runs; an unvouched binary is hashed as in EH1 (EH2) | U | fake sandbox | `tests/unit/harness_engines/test_engine_binary.py::test_eh2_15_*`..`test_eh2_17_*` |
| ENG-38 | happy | Real broker with the release pins: a session's binary check runs nothing in the sandbox; session start before and after, every engine, cold and warm, reported (EH2) | I, N-lat | real Docker, real images | `test_engine_binary_cache_docker.py::test_eh2_19_*`; `tests/unit/harness/engines/test_real_start_time.py::test_eh2_22_*` (`slow`) |
| ENG-39 | edge | An engine run's commits stay within the journal's limit: a save shown with more lines than a commit holds is sent as consecutive progress batches with their own command ids, and a kernel commit over the limit is consecutive commits with the transition last (PERF-A) | U | in-memory journal, recording presenter | `backend/python/tests/unit/harness_core/engines/durable/test_perfa_engine_commit_parts.py` |

**Fakes and contract suites.** `harness_core/testing` fake engine (`test_fake_engine_conformance.py`, 0 direct; subclasses), engine port
contracts `tests/unit/harness_core/engines/durable/test_port_contracts.py`, `test_controller_ports.py`; fake broker. Lacking a shared
contract run: `JournalEngineEventSink`, `PipesHubEngineAssembler` (R3 §3).

**Existing tests.** 110 files / 632 tests: unit 75/528, contract 2/6, component 1/8, integration-docker 18/59, e2e-browser 3/6,
gate-slow 11/25. Weak: `tests/unit/harness/engines/test_engine_instructions.py:28` (source-text assert).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| ENG-G1 | Loop-parent engine children skip the agent layer (R2 F5) | 5 | 4 | 20 | U | PR unit | ENG-20 | **closed by CPS (NO-6)** (`a168c7d5c`) |
| ENG-G2 | `JournalEngineEventSink` and `PipesHubEngineAssembler` not under a shared contract | 4 | 3 | 12 | K | PR unit | contract subclasses for both production adapters | none |
| ENG-G3 | Engine browser flows not in CI | 4 | 3 | 12 | Es | nightly e2e | ENG-25 in a CI e2e job (opencode, runc) | QA e2e lane in CI |
| ENG-G4 | reasoningEffort and deadline vs `maxWallS` (R2 F5, F17) | 4 | 3 | 12 | U | PR unit | ENG-21 | **closed by CPS (NO-1..NO-5) except the `minimal` enum (POL-G6)** (`a168c7d5c`) |
| ENG-G5 | Claude Code admission blocked; its matrix cells and pinned conformance cannot run | 3 | 4 | 12 | I, El | Docker lane, live | ENG-23 | **closed by QA-31** (CC1 admitted 2.1.294; its lanes rerun on the integration head) |
| ENG-G6 | Engine control credential and history from the agent shell (O-5, O-6) | 5 | 2 | 10 | N-sec | human pen test | ENG-18 | pen test |
| ENG-G7 | Replace weak test: source-text assert in `test_engine_instructions.py:28` | 2 | 3 | 6 | U | PR unit | ENG-27 behavioural | none |
| ENG-G8 | Engine queued forever when no `executor_engine` worker: user-visible deadline | 3 | 2 | 6 | U, Es | PR unit | ENG-22 deadline case | LAT1 |
| ENG-G9 | opencode and Codex checked only `--version` at session start, so a binary retagged under an allowlisted tag with the same version string ran (CC1 §10) | 5 | 2 | 10 | U, I | PR unit, Docker lane | ENG-31..ENG-36, SBX-31, SBX-32 | **closed by EH1** |

## MAC Model access routes and accounts

Built; owner SEC. Covers `backend/python/harness_core/access/`, `backend/python/app/harness/access/`, `backend/python/harness_engines/vendor_rules.json(.sig)`,
Node `application/model-access-*.ts`, `application/model-account-service.ts`, `domain/model-access.ts`, `domain/model-accounts.ts`,
`transport/model-account-routes.ts`.

**Behaviours.**
1. B1 Vendor rules are signed (Ed25519, release key id in code, not configuration); a tampered, unsigned or wrongly keyed document refuses every route (`vendor_rules_invalid`).
2. B2 An engine run pins one access route (org key, user's own key, org cloud account) under the rules, the offering (`AGENT_HARNESS_OFFERING`) and the org's narrowing; the gateway re-checks at every call.
3. B3 Another user's key is refused before any secret is read; each turn of a shared session uses its sender's account.
4. B4 A loop run naming a model account is refused `account_requires_engine`; a loop run without one pins as before.
5. B5 An unreadable org policy store refuses (503 `access_policy_unavailable`), never reads as nothing narrowed.
6. B6 Accounts are stored in the vault; keys never in logs or responses; the account UI is hidden until engine runs are on (R1 F16).
7. B7 An unknown offering value refuses to start.

**Levels.** U for rules, guard and resolver; K for access contracts; C for admin and account routes; X for gateway re-check after a narrowing mid-run; Es for AI accounts screens.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| MAC-01 | security | Tampered rules, another key, unknown id, unsigned → refused | U | — | `tests/unit/harness_core/access/test_vendor_rules.py::test_sg2_*`..`test_sg5_*` |
| MAC-02 | happy | Shipped rules verify against the shipped key; every vendor row decides a case | U | — | `test_vendor_rules.py::test_sg6_*`, `test_vr_every_vendor_row` |
| MAC-03 | security | Release key is code not configuration | U | — | **partial**: `test_vendor_rules.py:172` `test_sg7_*` asserts on source text (R3 risk 7) |
| MAC-04 | security | Another user's key refused before any secret read | U | — | `tests/unit/harness_core/access/test_access_guard.py::test_rs3_*` |
| MAC-05 | security | Org policy narrows at the next call (gateway re-check) | U | — | `test_access_guard.py::test_rs7_*` |
| MAC-06 | failure | Policy store unreadable → refuses | U | — | `test_access_guard.py::test_rs9_*`; Node `model-access-admission.test.ts` "AD-6 … 503" |
| MAC-07 | edge | Engine run on org default pins the concrete configured model | U | — | `model-access-admission.test.ts` "AD-1" |
| MAC-08 | security | Shared session: each turn picks its sender's account | U | — | `model-access-admission.test.ts` "SS2 MA-1"; `tests/unit/harness/test_ss2_credentials.py::test_ma2_*` |
| MAC-09 | edge | Loop run naming an account → `account_requires_engine` | U | — | `model-access-admission.test.ts` "AD-7" |
| MAC-10 | edge | Unknown offering refuses to start; unset is managed | U | — | `model-access-admission.test.ts` "OF-1" |
| MAC-11 | security | Account keys never in logs or route responses | U | — | `tests/modules/agent-harness/model-accounts-leak.test.ts`; `tests/unit/harness/credentials/test_leak.py::test_lk3_*`, `test_lk4_*` |
| MAC-12 | happy | Gateway end to end: pinned route resolves the right key per call | U | — | `tests/unit/harness/access/test_access_gateway_e2e.py` |
| MAC-13 | security | `accountKinds` narrowing is stored narrowed, not the wider set (R2 F20) | U | — | **partial:** QA-2 QA2-10 (vectors, both languages), QA2-11 (`qa2-model-access-narrowing.test.ts`: the decision holds the narrowed profile); admission still stores the wider set (QA2-O1) |
| MAC-14 | edge | AI accounts hidden until engine runs are on (R1 F16) | U, Es | — | **partial**: `frontend/app/(main)/workspace/sidebar/__tests__/sidebar-ai-accounts-visibility.test.tsx` |
| MAC-15 | edge | Dead export coverage: `model-account-providers.test.ts` tests `providersOfKind` with no src caller (R3 risk 8) | U | — | delete export and test |

**Fakes and contract suites.** `tests/unit/harness_core/access/test_access_contracts.py` (in-memory), signed test rule documents, vault fake.

**Existing tests.** 23 files / 170 tests: unit 19/151, contract 1/2, component 2/14, e2e-browser 1/3 (`ai-accounts.spec.ts`, dev-only).
Weak: `test_vendor_rules.py:172` (source text), `model-account-providers.test.ts` (dead code).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| MAC-G1 | `accountKinds` narrowing discarded (R2 F20) | 4 | 3 | 12 | U | PR unit | MAC-13 | **partly closed by QA-2** (the decision keeps it; admission and the pin do not store it yet: QA2-O1, CPD-5) |
| MAC-G2 | Narrowing mid-run reaches the gateway within NFR-23 (5 s / 30 s) | 4 | 2 | 8 | X | PR harness lane | org switches a row off during an engine turn; next call refused | CPD-6 |
| MAC-G3 | Replace weak test: source-text assert `test_vendor_rules.py:172` | 2 | 3 | 6 | U | PR unit | assert the loader ignores any env or config override | none |
| MAC-G4 | Remove dead-code coverage `model-account-providers.test.ts` | 1 | 5 | 5 | U | PR unit | delete export and test | none |

## SCH Scheduler

Built, Labs off by default (`ENABLE_SCHEDULED_RUNS`); owner HL. Covers Node `application/schedule-*.ts`, `application/scheduled-run-admission.ts`,
`domain/schedules.ts`, `adapters/mongo/mongo-schedule-store.ts`, `transport/schedule-routes.ts`, `adapters/messaging/broker-trigger-source.ts`.

**Behaviours.**
1. B1 A due slot admits exactly one run as the schedule's creator, with its own credential, the firing pinned; two firers on one store admit one run.
2. B2 Each firing re-authorizes: deleted actor disables, disabled or service-account actor pauses, revoked agent access pauses (`agent_forbidden`); flag off refuses `scheduled_runs_disabled` and keeps the schedule active.
3. B3 After an outage, `skip` records one skipped firing; `run_latest` admits one run for the latest slot; a firing claimed by a dead process is admitted once by the next.
4. B4 Transient failures retry with the same key; after five attempts refused `unavailable`; a refused firing is told to the actor at most once a day (UX3).
5. B5 Webhook: the right token fires once per window; wrong token and missing schedule answer alike; only the hash is stored.
6. B6 Connector triggers coalesce per window, start only while active, need an org admin.
7. B7 A firing the rollout says is legacy is refused `rollout_legacy`, not run on legacy; a schedule cannot be created when durable chat is off for the org (UX3).
8. B8 Budgets above the ceiling refused; unattended approvals reach the owner.

**Levels.** U with fake clock; K for the schedule store contract; C for routes; I for Mongo; X for two Node instances firing; Es for the schedules page.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| SCH-01 | happy | Due slot → one run as actor, credential minted, firing pinned | U | fake clock | `tests/modules/agent-harness/schedule-firer.test.ts` "SF-1" |
| SCH-02 | concurrency | Two firers on one store → one run | U | — | `schedule-firer.test.ts` "SF-2" |
| SCH-03 | failure | Outage: skip vs run_latest | U | — | "SF-3" (two cases) |
| SCH-04 | security | Disabled actor → no credential, paused; deleted → disabled; service account → paused | U | — | "SF-4" |
| SCH-05 | security | Agent access revoked → `agent_forbidden`, paused | U | — | "SF-5" |
| SCH-06 | edge | Flag off → `scheduled_runs_disabled`, no credential, schedule stays active | U | — | "SF-6" |
| SCH-07 | failure | Transient failure retries with the same key; five attempts → `unavailable`; dead claimer → next admits once | U | — | "SF-7", "SF-8" |
| SCH-08 | edge | Rollout says legacy → refused, not retried | U | — | "SF-10" |
| SCH-09 | security | Webhook token fires once per window; wrong token and missing schedule answer alike; token hash only | U | — | `schedule-service.test.ts` "SV-1: a webhook schedule…", "SV-7" |
| SCH-10 | security | Another member cannot read; admin may; never another org | U | — | `schedule-service.test.ts` "SV-2", "SV-3", "SV-4" |
| SCH-11 | edge | Durable off for the org → no schedule created (UX3 SE-1); refused firing told to the actor once a day (SE-2) | U | — | "UX3 SE-1"; `schedule-firer.test.ts` "UX3 SE-2" |
| SCH-12 | happy | Store contract on in-memory and Mongo | K, I | `support/schedule-store-contract.ts` | `schedule-store.test.ts`, `schedule-store.mongo.itest.ts` |
| SCH-13 | happy | A scheduled durable engine run in the Docker lane | I | real Docker | `tests/unit/harness/engines/test_scheduled_durable.py` |
| SCH-14 | concurrency | Two real Node instances, one Mongo → one firing per slot | X | two processes | **new** |
| SCH-15 | edge | Schedule editing and > 20 agents in the picker (R1 F15); time zone select (R1 F33) | U, Es | — | **partial**: `frontend/app/(main)/workspace/schedules/__tests__/schedules-page.test.tsx` |
| SCH-16 | edge | (target) firing resolves through `RunConfigPinner` like a send (FR-29) | U | — | **new** (CPD-5) |
| SCH-17 | edge | Schedule cost bound one value (schema 1000 vs domain 5, R2 F10) | U | — | **new** (CP7) |

**Fakes and contract suites.** `support/schedule-store-contract.ts` (13), in-memory schedule store, fake clock via sinon.

**Existing tests.** 15 files / 96 tests: unit 10/74, contract 1/13, component 1/5, integration 1/2, integration-docker 1/1, e2e-browser 1/1.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| SCH-G1 | Two real Node instances firing one slot | 4 | 2 | 8 | X | nightly fault | SCH-14 | none |
| SCH-G2 | Scheduled runs with approvals and flow guard end to end (unattended write) | 4 | 3 | 12 | Es | nightly e2e | must-run row `trigger=schedule_cron` with a sink tool | QA e2e lane in CI |
| SCH-G3 | Firing via the one pinner | 4 | 2 | 8 | U | PR unit | SCH-16 | CPD-5 |
| SCH-G4 | Schedule UI editing and paging | 2 | 3 | 6 | U | PR unit | SCH-15 | none |
| SCH-G5 | One cost bound | 2 | 3 | 6 | U | PR unit | SCH-17 | CP7 |

## ERA Erasure and retention (E7, E7B)

Built; owner SEC. Covers Node `application/session-erasure*.ts`, `application/retention-sweeper.ts`, `application/erasure-backfill.ts`,
`adapters/mongo/mongo-session-*eras*.ts`, `src/modules/enterprise_search/ops/erasure-backfill-cli.ts`; Python `app/harness/agent_deletion.py`.

**Behaviours.**
1. B1 Deleting a conversation enqueues one job, due at once; repeat asks enqueue nothing.
2. B2 The job runs every step once, in order (cancel runs, next runs, messages, citations, artifacts, workspaces, sandbox folders, journal, keys); a runner lacking a step is refused (no silent skip).
3. B3 A crash in any step resumes at that step; earlier steps do not run again; back-off from 5 s doubling to 5 min, never gives up; a lapsed lease cannot move the job.
4. B4 A live conversation (restored before or between steps) withdraws the job; nothing of it is erased.
5. B5 Retention: idle conversations of an org with a period (1–3650 days, config not env) expire and enqueue; a live run waits; never enqueues a deletion from before erasure existed.
6. B6 Backfill (E7B): dry run writes nothing and digests; execute refuses a non-matching digest, erases exactly the plan in batches of 100, audits each batch, never a live or marked conversation; a second execute is a no-op; a second executor is refused; exit 3 while the worker is busy.
7. B7 Agent erasure: queue, retry, crash window closed.
8. B8 Crypto-shred: an org or session key shred makes every sealed object unreadable.
9. B9 Logs carry ids, steps and counts, never content.

**Levels.** U for the job machine; K for agent-erasure queue and backfill contracts; I for Mongo itests; X for crash mid-erasure with real Node and broker; cohesion C-14 for store coverage (to be made behavioural); N-upg for backfill on an upgraded install.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| ERA-01 | happy | Delete → one job due now; again → nothing more | U | — | `tests/modules/agent-harness/session-erasure.test.ts` "ER-18" |
| ERA-02 | happy | Every step once, in order, completes with counts | U, I | — | `session-erasure.test.ts` "runs every step once, in order"; `session-erasure.mongo.itest.ts` |
| ERA-03 | failure | Runner lacking a step → refused | U | — | `session-erasure.test.ts` "refuses a runner that lacks a step" |
| ERA-04 | failure | Crash in step X → resumes at X; earlier steps not re-run | U | — | "ER-14: a crash in ${failing}…" |
| ERA-05 | failure | Lapsed lease holder cannot move the job | U | — | "ER-15" |
| ERA-06 | security | Live conversation before first step → job withdrawn; restored between steps → next step does not run | U | — | "a live conversation before the first step…", "live between steps…" |
| ERA-07 | failure | Failing lookup → retry, never erasure | U | — | "a failing lookup is a retry, never an erasure" |
| ERA-08 | happy | cancel_runs cancels each live run once with a replayable command id; drops waiting next runs first | U | — | "ER-2", "SS2 NR-9" |
| ERA-09 | happy | Sandbox folders erased through the broker | U, I | — | `tests/modules/agent-harness/sandbox-folder-erasure.test.ts`; `tests/unit/harness/sandbox_broker/test_docker_session_erase.py` |
| ERA-10 | happy | Retention: idle org conversations expire and enqueue; live run waits; org without a period expires nothing | U | — | `session-retention.test.ts` "RTN-2" (two cases) |
| ERA-11 | edge | Retention never enqueues a pre-erasure deletion (no `erasureRequestedAt`) | U | — | `session-retention.test.ts` "BF-1" |
| ERA-12 | happy | Backfill dry run counts, digests, writes nothing | U, K | `support/erasure-backfill-contract.ts` | `erasure-backfill.test.ts` "BF-4, BF-5" |
| ERA-13 | security | Execute with a non-matching digest → refused, writes nothing; stored items no longer hashing → refused before any batch | U | — | "BF-7", "BF-17" |
| ERA-14 | happy | Execute erases exactly the plan, audits each batch; second execute no-op | U, I | — | "BF-8, BF-9, BF-13", "BF-14"; `erasure-backfill.mongo.itest.ts` |
| ERA-15 | concurrency | Second executor refused; stale cursor refused | U | — | "BF-16" |
| ERA-16 | failure | Crash at each backfill point → resume completes once | U | — | `erasure-backfill.test.ts` "${point} (occurrence …)" |
| ERA-17 | happy | CLI `plan|execute|status` against a real replica set | I | — | `erasure-census.mongo.itest.ts` QA15-6 (production backend, census of every planned session; QA-15) |
| ERA-18 | security | Coverage: every store holding session data is a step (C-14) | cohesion | — | `tests/cohesion/c14-erasure-coverage.test.ts` (model introspection only; its source-text checks replaced by behaviour in QA-15); census verdict `erasure-census.test.ts` |
| ERA-19 | security | After erasure, no row in any session store for the session id (behavioural census over the real stores) | I | Mongo + blob + broker | `erasure-census.mongo.itest.ts` QA15-1, QA15-3; Python `test_erasure_real_graph.py` (QA-15) |
| ERA-20 | security | Shred: session key shred → workspace objects and snapshots unreadable | U, I | — | `workspace-service.test.ts` "NW-7"; `test_docker_snapshots.py::test_dk5_*` |
| ERA-21 | failure | Agent erasure crash window and retry | U, K, I | `support/agent-erasure-queue-contract.ts` | `agent-erasure-crash.test.ts`, `agent-erasure-retry.test.ts`, `agent-erasure-queue.mongo.itest.ts`, `agent-erasure.mongo.itest.ts` (QA-15) |
| ERA-22 | edge | Retention sweeper timers: start sweeps once, stop gives up the lease | U | sinon | **partial**: `session-retention.test.ts:298-306` uses real 5 ms timers (R3 risk 4) |
| ERA-23 | security | Logs carry ids and counts, never content or session ids in backfill | U | — | "ER-19"; `erasure-backfill.test.ts` "BF-22" |
| ERA-24 | failure | Erasure mid-run with Node restart and broker down: job waits, resumes, completes | X | real stack | **partial**: `erasure-census.mongo.itest.ts` QA15-4, QA15-5 at I level (QA-15); X open |
| ERA-25 | happy | Backfill on an upgraded install (pre-E7 deletions present) | N-upg | upgrade seed | **new** |
| ERA-26 | security | A run's idempotency key is stored as a keyed digest; after erasure its tombstone holds no caller text, an older build's raw key included; dedup unchanged | U, K, I | Mongo RS | `run-key-digest.test.ts` QA15F-1; `execution-store-contract.ts`, `child-run-contract.ts` QA15F-2; `erasure-census.mongo.itest.ts` QA15F-3, QA15F-4 (QA-15F) |
| ERA-27 | security | Chat attachments (record, file, edges, mapping, blobs) are erased with the conversation, only for the message's sender, resumably | U, K, I | Mongo RS, Neo4j, ArangoDB | `conversation-erasure-contract.ts` CE-5..CE-7; `harness-artifact-eraser.test.ts`, `test_artifact_erase_route.py` QA15F-7, QA15F-8; `test_erasure_real_graph.py` QA15F-9; `erasure-census.mongo.itest.ts` QA15F-10 (QA-15F) |
| ERA-28 | security | A service account's chat uploads (org grant, no `OWNER` edge) are erased with the person's conversation: the uploader (person, agent) is recorded on the record at upload and proves ownership; another person's upload, a member's and a knowledge-base record stay refused; an older unrecorded one is refused and counted | U, I | Neo4j, ArangoDB | `test_uploads.py` QA15G-1; `test_chatbot_document_attachments.py` QA15G-2; `test_artifact_erase_route.py` QA15G-3; `test_erasure_real_graph.py` QA15G-4; `scoped-request.test.ts` QA15G-10 (QA-15G) |
| ERA-29 | security | An uploader no longer in the graph: a recorded upload is erased by its conversation; a deactivated member's older upload by the `OWNER` edge the soft delete keeps; an older unrecorded upload of a removed user is refused and counted, also on the owner's own turn | U, I | Neo4j, ArangoDB | `test_artifact_erase_route.py` QA15G-3; `test_erasure_real_graph.py` QA15G-5 (QA-15G) |
| ERA-30 | edge | An upload named by two conversations, erased with the first: the other's messages mark it removed (org-scoped, resumable) and the chip reads "Attachment removed", opening nothing | U, K, I | Mongo RS | `conversation-erasure-contract.ts` CE-8, CE-9; `harness-artifact-eraser.test.ts` QA15G-7; `conversation-erasure.mongo.itest.ts` QA15G-8; `attachment-chip.test.tsx` QA15G-9 (QA-15G) |

**Fakes and contract suites.** `support/erasure-backfill-contract.ts` (7), `support/agent-erasure-queue-contract.ts` (10), in-memory
session erasure (no shared contract across in-memory and Mongo: R3 §3 lists session erasure as lacking a shared suite).

**Existing tests.** 22 files / 168 tests: unit 15/105, contract 2/17, cohesion 1/16, integration 3/28, integration-docker 1/2.
Weak: `session-retention.test.ts:298-306`
(real timers).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| ERA-G1 | Behavioural erasure census over real stores (replace C-14 source regex) | 5 | 3 | 15 | I | PR harness lane (itests) | ERA-19 | none — **closed by QA-15** |
| ERA-G2 | Session erasure has no shared contract over in-memory and Mongo | 4 | 3 | 12 | K | PR unit + itests | session-erasure store contract | none — **closed by QA-15** |
| ERA-G3 | Backfill CLI on a real replica set and on an upgraded install | 5 | 2 | 10 | I, N-upg | nightly | ERA-17, ERA-25 in `upgrade_smoke.sh` | none — **partly closed by QA-15** (ERA-17; ERA-25 open) |
| ERA-G4 | Erasure across a Node restart and broker outage | 4 | 2 | 8 | X | nightly fault | ERA-24 | none — **partly closed by QA-15** (I level) |
| ERA-G5 | Replace weak test: real timers in `session-retention.test.ts:298-306` | 2 | 4 | 8 | U | PR unit | ERA-22 with `sinon.useFakeTimers` | none |
| ERA-G6 | Backfill itests run only in path-filtered lanes (R3 §4) | 4 | 2 | 8 | I | nightly | run `erasure-backfill.mongo.itest.ts` nightly unconditionally | none — **closed by QA-15** |
| ERA-G7 | A run's caller-sent idempotency key survives on its erased tombstone (QA15-F3) | 4 | 3 | 12 | U, K, I | PR unit + itests | ERA-26 | none — **closed by QA-15F** |
| ERA-G8 | Chat attachments are not erased with the conversation (QA15-F4) | 5 | 3 | 15 | K, I | PR unit + itests + graph census | ERA-27 | none — **closed by QA-15F** |
| ERA-G9 | A service account's chat uploads are not erased with its conversation (QA-15F open item) | 4 | 3 | 12 | U, I | PR unit + graph census | ERA-28 | none — **closed by QA-15G** |
| ERA-G10 | An uploader no longer in the graph: their uploads are refused at erasure (QA-15F open item) | 3 | 2 | 6 | U, I | PR unit + graph census | ERA-29 | none — **closed by QA-15G** |
| ERA-G11 | An upload named by two conversations reads as a live link to nothing after the first is erased (QA-15F open item) | 3 | 3 | 9 | U, K, I | PR unit + itests | ERA-30 | none — **closed by QA-15G** |

## LAB Labs, flags and rollout

Built; owner PF. Covers `contracts/harness/flags.json`, Node `domain/rollout.ts`, `application/harness-rollout.ts`,
`src/modules/configuration_manager/`, `src/libs/utils/env-flags.ts`; Python `app/utils/env_flags.py`, `app/services/featureflag/`.

**Behaviours.**
1. B1 `flags.json` is the one flag matrix; FLAGS.md, code reads, Compose, env templates, Helm and `install.sh` agree (C-16).
2. B2 Every boolean env flag parses `1/true/yes/on` and `0/false/no/off` in any case; blank is the default; any other value stops the process at startup naming the flag (`InvalidEnvFlag`).
3. B3 Durable path for an org requires replica set, relay, worker, durable journal, kill switch off, Labs on, allowed model family, durable chat mode; the first unmet need decides the reason.
4. B4 Kill switch: from the next admission every turn goes legacy (`kill_switch`); runs in flight finish; queued next runs refused; reported by the effective-flags answer.
5. B5 Labs stores only changed flags; saves are compare-and-set with 409 `labs_changed`; a stored `false` for durable stays off and admins get a one-time notice.
6. B6 Every flag default comes from one table (`PLATFORM_FLAG_DEFAULTS`, C-16 FM-4b).
7. B7 Flags missing from the matrix (`MCP_ALLOW_CUSTOM_STDIO`, `PIPESHUB_AGENT_TRANSPORT`, `PIPESHUB_ENABLE_SKILLS`, `PIPESHUB_ENABLE_CODE_EXECUTION`, `PIPESHUB_SKILLS_*`) join it (R2 F13, CPD-0).
8. B8 (target, CP8) Worker features (compiler, model policy, provider state) get staged rollout pinned per run with a stable-hash canary.

**Levels.** S/cohesion for the matrix; U for parsing, rollout and Labs; I for Labs overrides on Redis (and etcd); X for the kill switch mid-flight; Es for Labs in the browser; N-upg for a stored Labs `false` across upgrade.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| LAB-01 | happy | Matrix vs FLAGS.md, code reads, Compose, env templates, Helm, install.sh | S | — | `tests/cohesion/test_c16_flag_matrix.py`; `tests/cohesion/c16-flag-matrix.test.ts` |
| LAB-02 | happy | Every Python flag has a probe; code default = matrix default | U | — | `tests/unit/harness/test_flag_matrix.py::test_fd1_*` |
| LAB-03 | failure | Malformed value → typed error naming the flag; worker and query service refuse to start | U | — | `test_flag_matrix.py::test_fm_f5_*`; Node `flag-matrix.test.ts` "${name} malformed is a typed error" |
| LAB-04 | happy | Shipped defaults on a replica set → agent chat durable | U | — | `tests/modules/agent-harness/flag-matrix.test.ts` "DUR3-G2" |
| LAB-05 | edge | Each unmet need → legacy with the matrix reason (standalone, PSA, org off, empty allowlist, planExecute) | U | — | `flag-matrix.test.ts` "${name} off → legacy", "a non-durable journal", "planExecute → legacy" |
| LAB-06 | happy | Kill switch on → legacy (`kill_switch`); in-flight runs reachable | U | — | `flag-matrix.test.ts` "kill switch on…"; `harness-rollout.test.ts` "reports org availability … under the kill switch" |
| LAB-07 | failure | Kill switch flipped mid-flight on a real stack: run finishes durable; next turn legacy with durable history; queued next run refused | X | real stack | **partial**: DUR3 tested mid-flight; `collaborator-turns-contract.ts` "DUR3-F7"; no X test in a lane |
| LAB-08 | concurrency | Two admins save Labs at once → one 409 `labs_changed`; kill-switch flip not lost (R2 F1) | U, I | Redis, etcd | `tests/modules/configuration_manager/ux2-labs-concurrency.test.ts`; `labs-overrides.redis.itest.ts`; etcd **new** |
| LAB-09 | edge | Stored durable `false` from before the release → stays off; one-time admin notice | U | — | `ux1-labs-overrides.test.ts`; `frontend/.../durable-default-notice.test.tsx` |
| LAB-10 | happy | Python Labs readers take defaults from one table | U | — | `tests/unit/services/featureflag/test_ux2_flag_defaults.py` |
| LAB-11 | edge | Retired `code_task` switch read by nothing | U | — | `test_flag_matrix.py::test_sr_2_*`; `labs-retired-code-task.test.ts` |
| LAB-12 | security | Labs save requires `config:write` + admin; a member → 403 | C | — | `tests/modules/configuration_manager/routes/cm_routes.guards.test.ts` |
| LAB-13 | edge | Unlisted flags (`MCP_ALLOW_CUSTOM_STDIO` et al.) fail C-16 once added to the scan | S | — | **new** (CPD-0) |
| LAB-14 | edge | Browser keeps cached effective flags after a kill switch flip (R1 F26) → refetch on focus or on `kill_switch` admission | U, Es | — | **new** |
| LAB-15 | happy | (target) staged rollout: stable-hash canary deterministic; stage pinned on the run | U, P | — | **new** (CP8) |
| LAB-16 | security | (target) Labs save appends `config.changed` in the same transaction (FR-26) | I | — | **new** (CPD-1) |

**Fakes and contract suites.** In-memory config document store, `fakeredis`; Labs overrides itest on real Redis; no etcd lane for Labs.

**Existing tests.** 42 files / 1014 tests (most are configuration_manager's general suite): unit 38/967, cohesion 2/40, integration 1/5,
e2e-browser 1/2. Harness-specific: about 10 files.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| LAB-G1 | Labs save not audited (R2 F1) | 4 | 4 | 16 | I | PR harness lane | LAB-16 | CPD-1 |
| LAB-G2 | Kill switch mid-flight not a lane test | 5 | 2 | 10 | X | nightly fault | LAB-07 on the fault stack | none |
| LAB-G3 | Labs CAS on etcd | 4 | 2 | 8 | I | nightly backend matrix | LAB-08 etcd | none |
| LAB-G4 | Unlisted flags (R2 F13) | 3 | 3 | 9 | S | PR unit | LAB-13 | **closed by CPS (FL-1..FL-3)** (`a168c7d5c`) |
| LAB-G5 | Stale effective flags in the browser (R1 F26) | 3 | 3 | 9 | U | PR unit | LAB-14 | none |
| LAB-G6 | Staged rollout | 3 | 2 | 6 | U | PR unit | LAB-15 | CP8 |

## SSN Shared sessions

Built (collaborator turns off by default); owner HL. Covers Node `application/session-*.ts`, `domain/session-*.ts`, `application/shared-*.ts`,
`adapters/mongo/mongo-session-backup.ts`, `ops/session-backup-cli.ts`.

**Behaviours.**
1. B1 Roles per share level: owner sends turns; collaborators send only with `ENABLE_COLLABORATOR_TURNS`; viewers are read-only; strangers get not found.
2. B2 A run acts as its sender ∩ the agent's grant; history is filtered per sender audience; personal memories and bindings never cross (NFR-18).
3. B3 A message to someone else's running task waits as a next run; when the run ends, waiting messages run as their senders in order; a sender who lost the role is refused at drain; the kill switch refuses queued messages.
4. B4 Only the owner starts engine work (`SHARED_SESSION_ENGINE`); roles undecidable → 503, nothing written.
5. B5 Continue privately: the fork copies the participant's turns, visible artifacts and citations; others become placeholders; idempotent by key; refused while a run is active (`RUN_ACTIVE`).
6. B6 Session import and backup round-trip; imported history labelled.
7. B7 (target, CPS/CPD-5) The next-run drain re-runs admission (agent access, ceilings, model access, memory) (R2 F6).

**Levels.** U for roles, audiences, folds; K for collaborator-turn and session-fork contracts; I for Mongo itests; X for drain across worker and Node restart; Es for a two-user browser session.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| SSN-01 | security | Collaborator with switch off → 403 `AGENT_HARNESS_COLLABORATOR_TURNS_DISABLED`; viewer → `AGENT_HARNESS_VIEWER_READ_ONLY`; stranger not found | U | — | `tests/modules/agent-harness/session-roles.test.ts` |
| SSN-02 | security | Engine work by a collaborator → 403 `SHARED_SESSION_ENGINE` before the store | U | — | `session-roles.test.ts` "EO-2" |
| SSN-03 | failure | Roles undecidable for engine work → 503, nothing written | U | — | `session-roles.test.ts` "EO-4" |
| SSN-04 | happy | Message to a running task waits; drained in order as its sender | K | `support/collaborator-turns-contract.ts` | "NR-1".."NR-4" |
| SSN-05 | security | Sender lost the role → refused at drain; next proceeds; switch off → refused | K | — | "NR-6" |
| SSN-06 | security | Kill switch on → waiting message refused, never a new durable run | K | — | "DUR3-F7" |
| SSN-07 | failure | Missed drain found by the sweeper | K | — | "NR-8" |
| SSN-08 | security | History audience: each sender's run sees only what its audience allows | U | — | `tests/unit/harness_core/durable/test_history_audience.py`; `tests/unit/harness/assembly/test_history_audience_assembly.py`; `session-audience.test.ts` |
| SSN-09 | happy | Fork copies own turns and visible artifacts; others are placeholders; idempotent by key; refused while active | K, I | `support/session-fork-contract.ts` | "FK-3".."FK-12"; `session-forks.mongo.itest.ts` |
| SSN-10 | failure | Failure inside the fork transaction leaves nothing; same request then completes | K | — | "FK-8" |
| SSN-11 | happy | Import and backup round trip on Mongo | I | — | `session-import.mongo.itest.ts`, `session-backup.mongo.itest.ts` |
| SSN-12 | security | Drain re-admits: agent access revoked, ceiling narrowed, model access changed since enqueue → refused with typed reason (R2 F6) | K | — | `backend/nodejs/apps/tests/modules/agent-harness/next-run-readmission.test.ts` NR-1..NR-6 (CPS, merged at `a168c7d5c`) |
| SSN-13 | happy | Two users in two browsers: Bob's turn waits behind Alice's run, then runs as Bob | Es | e2e stack, two contexts | **new** |
| SSN-14 | happy | Backup drill with engines (P9) | gate-slow | — | `tests/unit/harness/engines/test_p9_backup_drill.py` |

**Fakes and contract suites.** `support/collaborator-turns-contract.ts` (28), `support/session-fork-contract.ts` (18), in-memory
session forks; session participants lack a shared contract (`session-participants.test.ts` and `.mongo.itest.ts` are separate).

**Existing tests.** 27 files / 228 tests: unit 19/148, contract 2/46, integration 5/33, gate-slow 1/1.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| SSN-G1 | Next-run drain does not re-admit (R2 F6) | 5 | 4 | 20 | K | PR harness lane | SSN-12 | **closed by CPS (NR-1..NR-6)** (`a168c7d5c`) |
| SSN-G2 | No two-user browser test of collaborator turns | 3 | 3 | 9 | Es | nightly e2e | SSN-13 | QA e2e lane in CI |
| SSN-G3 | Participants without a shared contract | 3 | 2 | 6 | K | PR unit + itests | participants contract over in-memory and Mongo | none |

## LEG Legacy query path (today's loop)

Built (reference and kill-switch target); owner HL. Covers `backend/python/app/agents/` (agent loop, orchestrator, planner),
`backend/python/app/api/routes/chatbot.py`, `backend/python/app/api/routes/agent.py`, `backend/python/app/agent_loop_lib/`.

**Behaviours.**
1. B1 planExecute, Universal Agent Mode, image and project chats run on the legacy path today; so does every mode under the kill switch or a standalone Mongo.
2. B2 Requests the legacy path sends to the model equal the durable path's for the same inputs where both serve the mode (C-24).
3. B3 The legacy path cannot ask: an external write after a read (or in a conversation with history) is refused `approval_unavailable` (UX3).
4. B4 A conversation with durable turns continues on legacy with them as history after the kill switch.
5. B5 Tool sources (knowledge, MCP, toolsets, skills, run_code) behave as on durable for the modes they share.
6. B6 (target, CPD-10) Python stops its per-request agent, toolset, MCP, skill and feature-flag reads and assembles from `X-PipesHub-Resolved` (signed, compact JWS).

**Levels.** U for the loop and routes; replay for legacy-run recordings; eval (offline) for wave 1/2 parity; I for chat stream e2e in `tests/integration`; El for `integration-tests/ai_agents`.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| LEG-01 | happy | Agent loop chat stream end to end in process | I | — | `tests/integration/test_chat_stream_agent_loop_e2e.py` |
| LEG-02 | edge | Turn limit reached → answer-only turn | I | — | `tests/integration/test_agent_turn_limit_e2e.py` |
| LEG-03 | security | After a record read, an MCP or Slack write → `approval_unavailable`; before any read → runs | U | — | `tests/unit/agents/agent_loop/test_ux3_legacy_flow_guard.py` |
| LEG-04 | happy | Legacy run recorded and replayed in strict mode | replay | — | `tests/harness_replay/test_replay_legacy_run.py` |
| LEG-05 | happy | Durable and legacy produce equal model requests and outputs for wave-1 and harness cases (C-24) | cohesion, eval | — | `tests/cohesion/test_c24_durable_wire_parity.py` (`slow`, over the parity cases in `tests/support/parity_cases/`) |
| LEG-06 | edge | Kill switch: conversation with durable turns continues on legacy using them as history | X | — | **partial**: `tests/modules/agent-harness/flag-matrix.test.ts` (admission); history continuity **new** |
| LEG-07 | happy | planExecute and Universal Agent Mode answer through the existing stream routes, no wasted durable POST (R1 F8) | C, Es | — | `qa8-routing-vectors.http.test.ts` (QA8-8, C); `lat1-chat-turn.http.test.ts` OR-3; Es with EM-10 (QA-5) |
| LEG-08 | happy | Project chat decided server-side → legacy | U | — | `tests/modules/agent-harness/ux3-admission-placement.test.ts`; `frontend/.../project-scope.test.ts` |
| LEG-09 | happy | Live agent harness and tools against real models | El | — | `integration-tests/ai_agents/integration_test_agent_harness_live.py`, `integration_test_agent_tools_live.py` |
| LEG-10 | security | (target) Legacy query path assembles from a signed `X-PipesHub-Resolved`; unknown `kid`, expired or tampered → refused | U | — | **new** (CPD-10) |
| LEG-11 | N-lat | Legacy TTFT after CPD-10 lower than before (net reduction, NFR-3) | N-lat | `bench_harness.py` | **new** |

**Fakes and contract suites.** `ScriptedTransport` (`tests/unit/agents/adapter/support/scripted_transport.py`), the replay world,
`tests/support/fake_record_graph.py`. R3 risk 6: unspecced graph mocks in shared worlds (`tests/unit/harness/assembly/world.py:309`,
`tests/harness_replay/durable_driver.py:100-122`) affect parity tests.

**Existing tests.** 405 files / 10,854 tests mapped (the whole legacy agent tree): unit 368/10,543, contract 4/68, eval 24/177,
replay 1/7, integration 2/15, live 4/29.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| LEG-G1 | Kill-switch history continuity on a real stack | 4 | 2 | 8 | X | nightly fault | LEG-06 | none |
| LEG-G2 | One-request routing for legacy-only modes | 3 | 3 | 9 | C | PR unit | LEG-07 | LAT1 |
| LEG-G3 | Signed resolved header on the legacy path | 5 | 2 | 10 | U | PR unit | LEG-10 | CPD-10 |
| LEG-G4 | Spec the graph mocks in shared worlds (R3 risk 6) | 3 | 3 | 9 | U | PR unit | `AsyncMock(spec=IGraphDBProvider)` or `fake_record_graph` | none |
| LEG-G5 | Legacy latency A/B after CPD-10 | 3 | 2 | 6 | N-lat | weekly perf | LEG-11 | CPD-10 |

## CON Cross-language contracts and codegen

Built; owner HL. Covers `contracts/harness/` (schemas, vectors, codegen, `generate.sh`, replay corpora), Node `src/modules/agent-harness/contracts/`,
Python `harness_core/contracts/`, `app/harness/contracts/`.

**Behaviours.**
1. B1 JSON Schemas are the source; Python and TypeScript are generated; regeneration leaves no diff (C-5 drift, both halves).
2. B2 Valid fixtures round-trip byte-equal Python → TS → Python; invalid fixtures are rejected by both with the same error path; generated instances accepted by both.
3. B3 Canonical JSON and digests agree across languages (vectors).
4. B4 `harness_core` imports no `app`, `docker`, `fastapi`, `dependency_injector` (C-3); one definition per shared type (C-1); every port has an in-memory adapter and contract suite (C-2).
5. B5 Fold version, run-spec fold pin and replay corpus agree (C-5); replay of golden journals stays equal (replay-compat both sides).
6. B6 Node's known worker features equal the worker's advertisement (C-7); `nodeCapabilities` handshake makes both upgrade orders safe (UX2).
7. B7 Hand-mirrored contracts (run enums, envelope, mounts, error codes, worker features, frontend DTOs) become generated (R3 M15, WP-11, CPD-0).
8. B8 Slow-lane registry (C-25) and `--strict-markers` keep markers honest.

**Levels.** S/cohesion for drift and boundaries; U for schemas and vectors; K for replay-compat; N-upg for schema version evolution.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| CON-01 | happy | Schemas valid; fixtures accepted/rejected | U | — | `tests/unit/contracts_harness/test_c1_schemas.py` |
| CON-02 | happy | Round trip Python ↔ TS byte-equal | U | — | `tests/unit/contracts_harness/test_c2_roundtrip.py` |
| CON-03 | happy | Canonical vectors equal in both languages | U | — | `test_c3_canonical_vectors.py`; Node `contracts/canonical-json.test.ts` |
| CON-04 | property | 500 generated instances per schema accepted by both | P | hypothesis-jsonschema | `test_c4_generative.py` |
| CON-05 | failure | Codegen drift: stale generated code fails | S | — | `test_c5_codegen_drift.py` (TS half needs node in the job, R2 F13) |
| CON-06 | security | Core neutral and import boundary | S | — | `test_c6_core_neutral.py`; `tests/unit/harness_core/test_import_boundary.py`; `tests/cohesion/test_c3_c7_contracts_and_versions.py` |
| CON-07 | happy | Port registry: every port has in-memory adapter + suite (C-2) | cohesion | — | `tests/cohesion/test_c2_port_contracts.py` (requires only in-memory, R3 §4) |
| CON-08 | happy | Replay-compat on both sides from a recorded ref | K | — | `tests/contracts/test_replay_compat.py`; `tests/contracts/replay-compat.test.ts` |
| CON-09 | edge | Worker features: Node list = Python advertisement | U | — | `tests/modules/agent-harness/worker-compat.test.ts`; C-7 in `test_c3_c7_*` |
| CON-10 | edge | Error codes and run enums: one generated source; `payload_store_unavailable` present in all copies (R3 M15) | S | — | **new** (CPD-0 / WP-11) |
| CON-11 | edge | Frontend DTOs generated; `tsc` fails on drift | S | — | **new** (CPD-0) |
| CON-12 | edge | C-2 extended: production adapters must also run the contract | cohesion | — | **new** |
| CON-13 | edge | `schemaVersion` bump requires an upcaster and an N−1 golden fixture | U | — | **partial**: replay-compat; explicit upcaster test **new** |
| CON-14 | happy | Slow lanes registered; markers strict | cohesion | — | `tests/cohesion/test_c25_slow_lanes.py`; `pytest.ini --strict-markers` |
| CON-15 | happy | The events one commit carries is one contract value, `journal.maxCommitEvents` in `contracts/harness/core/policy/release.json`, generated for Node and Python; any other definition fails C-34 (PERF-A, QA22-F1) | cohesion, S | — | `backend/python/tests/cohesion/test_c34_one_definition.py`; `backend/nodejs/apps/tests/modules/agent-harness/perfa-commit-limit.test.ts`; `backend/python/tests/unit/harness_core/durable/test_perfa_commit_parts.py::test_perfa_1_*` |

**Existing tests.** 26 files / 107 tests: unit 20/86, contract 2/9, cohesion 4/12.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| CON-G1 | Hand-mirrored enums and error codes already drifted (R3 M15) | 4 | 4 | 16 | S | PR unit | CON-10 | CPD-0 |
| CON-G2 | C-2 requires only in-memory adapters | 4 | 3 | 12 | cohesion | PR unit | CON-12 | none |
| CON-G3 | TS half of codegen drift may skip in the Python job (R2 F13) | 3 | 3 | 9 | S | PR unit | install node in the job; fail on skip | CPD-0 |
| CON-G4 | Frontend DTOs hand-written | 3 | 3 | 9 | S | PR unit | CON-11 | CPD-0 |
| CON-G5 | Upcaster rule per schema bump | 3 | 2 | 6 | U | PR unit | CON-13 | none |

## OBS Observability and logging

Built (OTel off by default); owner PF. Covers `backend/python/harness_core/telemetry/`, `backend/python/app/harness/telemetry.py`,
`backend/python/harness_core/logs.py`, Node `application/harness-tracing.ts`, `application/harness-log.ts`, `adapters/telemetry/`.

**Behaviours.**
1. B1 With `HARNESS_OTEL=true`, Node records the admission span; the worker and gateway record `invoke_agent`, sandbox acquire, `execute_tool` and chat spans (GenAI conventions); off means no OpenTelemetry import.
2. B2 Spans carry ids, enums, token counts and timings, never content, unless `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`.
3. B3 Lifecycle logs carry correlation fields and never content, secrets or paths; claim refusals logged once with a reason.
4. B4 `pipeshub_agent_chat_turns_total{path}` and run-claim metrics show the path and the kill switch effect.
5. B5 Opik sits behind `HarnessTracer` (R3 M13, open).

**Levels.** U for tracers, log shapes and redaction; C for refusal logging; X for log records across a crash (fault suite).

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| OBS-01 | happy | OTel on: spans per stage with GenAI attributes | U | in-memory exporter | `tests/unit/harness_core/telemetry/test_tracer.py`; Node `harness-tracing.test.ts` |
| OBS-02 | security | Content off by default; on only with the opt-in | U | — | **partial**: `test_tracer.py` (check content flag case) |
| OBS-03 | happy | OTLP/HTTP export to a collector | U | — | `otlp-http-tracer.test.ts` |
| OBS-04 | security | Logs carry ids, never content or secrets | U | — | `tests/unit/harness/test_log_redaction.py`; `harness-log.test.ts`; `projection/transcript-logs.test.ts` |
| OBS-05 | happy | Path metrics after kill switch | U | — | `dur3-chat-metrics.test.ts` |
| OBS-06 | failure | Claim refusal logged once with reason | C | — | `claim-refusal-logging.http.test.ts` |
| OBS-07 | failure | Log records survive a crash point and carry the attempt | X | fault stack | `integration-tests/harness/fault/test_log_records.py` |
| OBS-08 | security | Canary scan of span exports over a full run | X | canary | **new** (with TSC-22) |
| OBS-09 | edge | Opik behind `HarnessTracer`: kernel imports no Opik | U | — | **new** (R3 M13) |
| OBS-10 | happy | Control-plane metrics: cache hit ratio, resolve latency, notifier lag, fail-closed refusals (FR-25) | U | — | **new** (CPD-5/CPD-6) |

**Existing tests.** 13 files / 57 tests: unit 11/54, component 1/1, fault 1/2.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| OBS-G1 | Span content leak under a full run | 4 | 2 | 8 | X | PR harness lane | OBS-08 | none |
| OBS-G2 | Control-plane health metrics | 3 | 3 | 9 | U | PR unit | OBS-10 | CPD-5 |
| OBS-G3 | Opik dependency inversion | 2 | 3 | 6 | U | PR unit | OBS-09 | none |

## FEC Frontend chat surfaces

Built; owner FE. Covers `frontend/app/(main)/chat/` (page, `streaming.ts`, `durable-run-stream.ts`, `agui-event-handler`, `api.ts`, message-area cards),
`frontend/lib/`.

**Behaviours.**
1. B1 One reducer (`createAGUIEventHandler`) renders durable and legacy frames; live render equals reloaded transcript.
2. B2 The durable stream reconnects with `Last-Event-ID`, no duplicate or missing text, through restarts while the tab is visible; 401 ends with session expired; 403/RUN_ERROR final.
3. B3 Stop is prompt and the stopped answer equals the saved one (R1 F5, UX3).
4. B4 Approval, unknown-effect, ask-user and engine cards show what, where and why with redacted payloads; actions are keyboard reachable and announced (R1 F20).
5. B5 Steering: placeholder says the message joins the running task; a message the run never used offers resend (R1 F19).
6. B6 Errors are codes translated on the client; no English-only strings or fixed `en-US` formats (R1 F22, F24, F25, F31).
7. B7 Project chats and regenerate route by the server (UX3).
8. B8 Citations render the same while streaming and final (R1 F25).

**Levels.** U (vitest, jsdom) for reducers, stream client, cards; K for golden frames fed through the reducer; Es (Playwright) for journeys; N-ux for axe and i18n parity.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| FEC-01 | happy | Stream a run from first frame to `RUN_FINISHED` | U | — | `frontend/app/(main)/chat/__tests__/durable-run-stream.test.ts` "streams a run…" |
| FEC-02 | edge | Reconnect mid-message with `Last-Event-ID`, no duplicate or missing text | U | — | `durable-run-stream.test.ts` "reconnects mid-message…" |
| FEC-03 | failure | Rolling restart longer than the old budget: keeps reconnecting while visible; resumes on `online` (UX3 RC-1/2) | U | fake timers | `durable-run-stream.test.ts` "UX3 RC-1", "UX3 RC-2" |
| FEC-04 | failure | 401, 403, 404, `RUN_ERROR` → final states | U | — | `durable-run-stream.test.ts` (five cases) |
| FEC-05 | happy | Two tabs on one run render the same answer | U | — | `durable-run-stream.test.ts` "two tabs on one run…" |
| FEC-06 | happy | Golden SSE streams fed through the reducer equal snapshots (TEST-STRATEGY §3.2 vitest twin) | K | goldens | **partial**: `agui-event-handler.test.ts`, `agui-durable-handler.test.ts` (hand-built frames) |
| FEC-07 | property | Live `parts[]` = reloaded transcript for random journals (transcript parity) | P | fast-check | **new** |
| FEC-08 | happy | Stop: prompt; stopped answer = saved | U, Es | — | `streaming-stop.test.ts`; browser **new** |
| FEC-09 | security | Approval card redacts per the shared vector; never raw args (R3 M10) | U | token vector | **done (QA-11)**: `approval-preview.test.ts`, `approval-card-redaction.test.tsx` |
| FEC-10 | happy | UX3 chat paths: project scope server-side, regenerate durable | U | — | `ux3-chat-paths.test.ts`; `project-scope.test.ts` |
| FEC-11 | happy | Engine card restored on reload with waiting-for-approval state (R1 F11, F21) | U | — | `code-task-card.test.tsx`, `durable-run-ui.test.tsx`, `durable-timeline.test.ts` |
| FEC-12 | a11y | Pending approval announced in a live region, scrolled into view, title badge (R1 F20) | U, N-ux | axe | **new** |
| FEC-13 | a11y | Artifacts panel header is a button with `aria-expanded` (R1 F34) | U | — | **new** |
| FEC-14 | i18n | Run error codes translated; statuses ("Thinking…") from keys (R1 F22) | U | i18n parity scripts | **new** |
| FEC-15 | i18n | Cost and tokens formatted with app locale, not `en-US` (R1 F24) | U | — | **new** |
| FEC-16 | i18n | Citation spacing equal streaming and final; generic link words localized (R1 F25) | U | — | **partial**: `utils/__tests__/process-markdown-content.test.ts` |
| FEC-17 | edge | Steering placeholder and "Send as new message" (R1 F19) | U | — | **partial**: `steering.test.ts` |
| FEC-18 | happy | Durable chat journeys in Playwright (stream, approval card, run UI, engine) run in CI | Es | e2e stack | **partial**: `frontend/tests/e2e/chat/chat-durable-stream.spec.ts`, `chat-approval-card.spec.ts`, `chat-durable-run-ui.spec.ts` (dev-only `deployment/e2e/run.sh`) |
| FEC-19 | edge | Reconnect latency to first new frame ≤ 3 s (SLO #8) | N-lat | Playwright | **new** |
| FEC-20 | failure | `use-run-timeline.ts:52-80` stale-response race; `durable-run-stream.ts:110-122` abort listener leak (R3 minor) | U | — | **new** |
| FEC-21 | a11y | Activity panel (UX5): every state (empty, running, finished, failed, approval waiting/denied, reconnecting, unavailable); auto-scroll pauses on scroll-up with 'Jump to latest'; a polite live region announces state changes, never output; 10,000 lines render a bounded window; toggle `aria-expanded`/`aria-controls`, Escape returns focus; read-only | U | jsdom, fake timers | `frontend/app/(main)/chat/components/__tests__/ux5-activity-panel.test.tsx`, `frontend/app/(main)/chat/__tests__/ux5-activity.test.ts` (FE-1..FE-7, FU-8) |
| FEC-22 | happy | Follow-up chips under the latest completed answer only; a click fills and focuses the input and never sends; engine action chips (open the changed files, run the tests again) | U | real `MessageList` + `ChatInput` | `frontend/app/(main)/chat/components/message-area/__tests__/ux5-followup-chips.test.tsx` (FU-6, FU-7) |
| FEC-23 | happy | Activity panel and chips in the browser (mocked SSE) | Es | Playwright | `frontend/tests/e2e/chat/chat-activity-panel.spec.ts` (FE-9; not run yet) |

**Existing tests.** 95 files / 1,118 tests: unit 81/1,000, e2e-browser 14/118. Harness Playwright specs skip outside `run.sh`;
Playwright `retries: 2` on CI (`frontend/playwright.config.ts:37`).

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| FEC-G1 | Harness browser specs do not run in CI | 4 | 5 | 20 | Es | nightly e2e (CI) | FEC-18 on a CI job running `deployment/e2e/run.sh` (retries 0) | QA e2e lane in CI |
| FEC-G2 | Approval card redaction drift (R3 M10) | 5 | 3 | 15 | U | PR unit | FEC-09 | none |
| FEC-G3 | Transcript parity property (live = reload) | 3 | 3 | 9 | P | PR unit | FEC-07 | none |
| FEC-G4 | Golden replay vitest twin | 3 | 3 | 9 | K | PR unit | FEC-06 | none |
| FEC-G5 | i18n of run errors, statuses, numbers, citations (R1 F22, F24, F25, F31) | 2 | 4 | 8 | U | PR unit | FEC-14..16 | none |
| FEC-G6 | Accessibility of approval arrival and artifacts panel (R1 F20, F34); no axe lane | 2 | 4 | 8 | U, N-ux | PR unit + nightly | FEC-12, FEC-13 with axe-core | none |
| FEC-G7 | Stream client races (R3 minor) | 2 | 3 | 6 | U | PR unit | FEC-20 | none |
| FEC-G8 | Reconnect latency SLO #8 | 3 | 2 | 6 | N-lat | nightly e2e | FEC-19 | none |

## FEA Frontend workspace and admin surfaces

Built; owner FE. Covers `frontend/app/(main)/workspace/` (labs, approval-policy, approvals, unknown-effects, ai-access, ai-accounts, schedules,
memories, mcp-servers, skills, harness-ui) and `frontend/app/(main)/agents/` (agent builder).

**Behaviours.**
1. B1 Admin screens show the server's state with a busy state, save with CAS and render a clear 409 conflict.
2. B2 Refusals render as translated codes with parameters, never server English (R1 F31).
3. B3 Sidebar shows harness pages only when the feature and the role allow (approvals, AI accounts, skills, U4 pages).
4. B4 Labs: notice for a stored durable `false`; reset to default; toast on every save; confirm the two org-wide switches (R1 F30).
5. B5 Unknown-effects console translates codes, shows destination, deadline and run link (R1 F27).
6. B6 MCP manifest approval shows approver names, notifies on drift (R1 F28).
7. B7 Schedules: editable, > 20 agents, time zone select, next-run preview, history busy state (R1 F15, F33).
8. B8 (target, CPD-11) Capabilities area generated from schemas: catalog, installations, approvals inbox, policy sections, history and rollback, explain.

**Levels.** U (vitest) per screen; Es for admin journeys; N-ux for axe and i18n.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| FEA-01 | happy | Labs overrides: only changed flags saved; reset to default | U | — | `workspace/labs/__tests__/labs-overrides.test.tsx` |
| FEA-02 | concurrency | Labs 409 `labs_changed` rendered with reload | U | — | `labs/__tests__/ux2-labs-conflict.test.tsx` |
| FEA-03 | edge | Durable-default notice for a stored `false` | U | — | `workspace/components/__tests__/durable-default-notice.test.tsx` |
| FEA-04 | happy | Approval policy edit and save | U | — | `approval-policy/__tests__/approval-policy.test.tsx` |
| FEA-05 | security | Approvals queue: member sees own runs only; admin deny-only | U | — | `backend/nodejs/apps/tests/modules/agent-harness/approvals-queue.test.ts` AQ-0 |
| FEA-06 | happy | Unknown-effects console resolves | U | — | `unknown-effects/__tests__/unknown-effects.test.tsx` |
| FEA-07 | i18n | Console codes translated, destination and deadline shown (R1 F27) | U | — | **new** |
| FEA-08 | happy | Engine profile editor and org AI access | U | — | `ai-access/__tests__/engine-profile-editor.test.tsx`, `org-ai-access.test.tsx` |
| FEA-09 | edge | Sidebar visibility per feature and role | U | — | `sidebar/__tests__/sidebar-*-visibility.test.tsx` (5 files) |
| FEA-10 | happy | MCP manifest approval screen | U, Es | — | `mcp-servers/team/__tests__/mcp-manifest.test.tsx`; `tests/e2e/workspace/u4-manifest-approval.spec.ts` (dev-only) |
| FEA-11 | i18n | Refusal helper renders codes with parameters (R1 F31) | U | — | **partial**: `harness-ui/__tests__/refusal.test.ts` |
| FEA-12 | i18n | Labs flag labels from translation keys (R1 F30); i18n parity for harness screens | U, S | `frontend/scripts/check-i18n-parity.mjs` | **partial**: parity script exists; harness keys not checked |
| FEA-13 | a11y | Axe: no serious or critical violations on labs, approvals, unknown effects, schedules, memories, AI access | N-ux | axe-core in Playwright | **new** |
| FEA-14 | happy | Schedules page editing, > 20 agents, time zone select (R1 F15, F33) | U | — | **partial**: `schedules/__tests__/schedules-page.test.tsx` |
| FEA-15 | happy | Admin journeys in Playwright (labs, AI accounts, schedules) run in CI | Es | — | **partial**: `tests/e2e/workspace/labs.spec.ts`, `ai-accounts.spec.ts`, `u4-schedules.spec.ts` (harness ones dev-only) |
| FEA-16 | happy | (target) Capabilities area: install → approve → grant → chat; 409 conflict; rollback; explain; connect-account card | Es | — | **new** (CPD-11) |
| FEA-17 | edge | Shared hooks: `useDirectoryNames` replaces six copies (R3 D21) | U | — | **new** (WP-14) |

**Existing tests.** 88 files / 713 tests: unit 76/676, e2e-browser 12/37.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| FEA-G1 | Harness admin journeys not in CI | 3 | 5 | 15 | Es | nightly e2e (CI) | FEA-15 | QA e2e lane in CI |
| FEA-G2 | No accessibility lane | 2 | 4 | 8 | N-ux | nightly e2e | FEA-13 | none |
| FEA-G3 | i18n of codes, labels and refusals (R1 F27, F30, F31) | 2 | 4 | 8 | U, S | PR unit | FEA-07, FEA-11, FEA-12 | none |
| FEA-G4 | Capabilities area journeys | 4 | 2 | 8 | Es | nightly e2e | FEA-16 | CPD-11 |
| FEA-G5 | Schedules UX gaps | 2 | 3 | 6 | U | PR unit | FEA-14 | none |

## DEP Deployment, migrations and upgrade

Built; owner PF. Covers `deployment/docker-compose/` (base, overlays, `install.sh`), `deployment/helm/`, `deployment/sandbox*/`,
`backend/python/app/harness/migrations.py`, Node `adapters/mongo/*migration*.ts`, `harness-migrations.ts`.

**Behaviours.**
1. B1 Compose base runs relay and worker (model gateway follows), replica set, sandbox profile per `SANDBOX_MODE`; Helm `agentHarness.enabled=true`, `mongodb.architecture=replicaset`, broker mode.
2. B2 Defaults in Compose, env templates, Helm and `install.sh` equal `flags.json` (C-16).
3. B3 D2 migrations: dry run, `--apply`, `--revert`, ledger key, idempotent; index migrations named and registered (C-15).
4. B4 Upgrade order migrations → workers → Node; either order of worker and Node upgrade is safe; rollback leaves a newer run waiting for a newer worker; a rolling deploy under load stalls no stream past the bound.
5. B5 Footprint within budget (`eval` total ≤ 7.5 GB, app ≤ 4.5 GB; SLO #15–16).
6. B6 Journal failover on a 3-member replica set; standalone conversion idempotent.
7. B7 Backup and restore drill.
8. B8 Helm: internal listener bound to 127.0.0.1; uid 1000 starts Node (D3); values refs valid.

**Levels.** deployment scripts (Compose, Helm on kind); I for migrations on Mongo; X/N-upg for upgrades and failover (fault suite); N-load for footprint.

**Cases.**

| # | Type | Case (given / when / then) | Level | Fakes or suite | Exists |
|---|---|---|---|---|---|
| DEP-01 | happy | Published hub install per profile (slim, full, eval, arango) comes up healthy | deployment | — | `deployment/docker-compose/tests/published_hub_smoke.sh` (`published-hub-smoke.yml`) |
| DEP-02 | happy | Footprint within budget (eval) | N-load | — | `deployment/docker-compose/tests/footprint.sh`; `footprint.py check` in the eval job |
| DEP-03 | happy | Replica set default, standalone conversion, rerun idempotent | deployment | — | `mongodb_replset_test.sh` (`agent-harness-infrastructure.yml`) |
| DEP-04 | failure | Journal failover on step-down | deployment, I | — | `agent_harness_journal_failover_test.sh`; `journal-failover.rs3.itest.ts` |
| DEP-05 | happy | Helm chart lint and kind install | deployment | kind | `deployment/helm/tests/check_chart.sh`, `kind_smoke.sh` |
| DEP-06 | happy | Helm harness env and Node logging (uid 1000) | U | — | `deployment/helm/tests/test_harness_env.py`, `test_node_logging.py` |
| DEP-07 | happy | Migrations registered and applied on a fresh RS; revert | I | — | `tests/modules/agent-harness/harness-migrations.mongo.itest.ts`; `tests/cohesion/test_c15_data_migrations.py`; `c15-index-migrations.test.ts` |
| DEP-08 | happy | Tool identity migration dry run, apply, revert | U | — | `tests/unit/harness/tool_identity/test_migration.py` |
| DEP-09 | failure | Rollback leaves a newer run waiting for a newer worker | X | fault stack | `integration-tests/harness/fault/test_upgrades.py::test_ru5a_*` |
| DEP-10 | failure | Rolling deploy under load stalls no stream past the bound | X | — | `test_upgrades.py::test_ru4a_*` |
| DEP-11 | failure | Full control-plane (Node) outage → recovery (C-13 holdoff) | X | — | `test_upgrades.py::test_c13_recovery_after_a_full_control_plane_outage` |
| DEP-12 | edge | Worker newer than Node and Node newer than worker: nothing refused that should run (UX2 `nodeCapabilities`) | U, X | — | `tests/unit/harness/test_ux2_node_capabilities.py`; X both orders **partial** |
| DEP-13 | happy | Upgrade smoke with in-flight durable runs, from the previous release | N-upg | `upgrade_seed.py` | **partial**: `deployment/docker-compose/tests/upgrade_smoke.sh` (no in-flight run) |
| DEP-14 | happy | Backup and restore drill | gate-slow | — | `tests/unit/harness/engines/test_p9_backup_drill.py` |
| DEP-15 | happy | Helm with the harness on: a durable chat turn on kind (fake LLM) | Es | kind | **new** |
| DEP-16 | edge | Compose overlays: egress-tunnels overlay gives only the proxy an off-host network | deployment | — | **partial**: `sandbox_plane_test.sh` |
| DEP-17 | happy | Installer test: `install.sh` writes only `SANDBOX_MODE`, `COMPOSE_PROFILES`, `ROTATE_SIGNING_SECRETS` | deployment | — | `installer_test.sh` |
| DEP-18 | happy | (target) Control-plane migrations (CPD-4, CPD-7, CPD-8): dry run, apply, dual-write one release, flip, revert without data loss | I, N-upg | — | **new** |
| DEP-19 | happy | Helm agent-sandbox on a real K8s cluster with network policies | I | K8s | **new**; needs the K8s lane |

**Existing tests.** 30 files / 75 tests: deployment 19 scripts, unit 4/59, cohesion 2/7, integration 1/5, fault 1/3, gate-slow 1/1.

**Gaps.**

| Gap | What is missing | Impact | Likelihood | Score | Level | Lane | Proposed test | Depends on |
|---|---|---|---|---|---|---|---|---|
| DEP-G1 | Upgrade smoke with in-flight durable runs, both orders | 4 | 3 | 12 | N-upg | release, nightly | DEP-13 + DEP-12 on the fault stack | none |
| DEP-G2 | Control-plane migrations dual-write, flip, revert | 5 | 3 | 15 | I, N-upg | PR harness lane + release | DEP-18 | CPD-4 |
| DEP-G3 | Helm with harness on: a durable turn on kind | 3 | 3 | 9 | Es | weekly `helm-chart.yml` | DEP-15 | none |
| DEP-G4 | Real K8s with agent-sandbox | 4 | 2 | 8 | I | K8s lane (not in the default CI) | DEP-19 | none |
