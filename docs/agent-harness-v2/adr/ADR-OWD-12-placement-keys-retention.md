# ADR-OWD-12 — Run placement, tenancy keys and retention

## Context

[OWD-12](../one-way-doors.md) settles where sandboxes, snapshots, transcripts and model calls run and are stored, and how they are encrypted and retained. Once private data is written to the wrong region or under a shared key, remediation means deletion and incident handling.

## Decision

Adopt OWD-12:

- Every stored object carries `org_id` and region.
- Per-tenant keys for transcripts and snapshots, with one wrapped data key per session workspace and erasure by crypto-shred (the implementation plan D33; the v2 architecture report D27).
- Retention is per tenant, and deletion is verifiable.
- Engine snapshots default to a short TTL (7 days) unless the org configures otherwise.
- Run placement and model-call placement are extension points; their defaults do nothing:
  - A process's region is `default` unless an extension point names it: Node `HarnessEdition.region` (read by `useRegionSource`), Python `RegionPort` (`backend/python/harness_core/region.py`, read by `backend/python/app/harness/placement.py`).
  - `RunPlacement` (`backend/nodejs/apps/src/modules/agent-harness/ports/run-placement.ts`, token `AGENT_HARNESS_RUN_PLACEMENT`). Unbound, every run is placed here.
  - Node `ModelPlacementSource` (`backend/nodejs/apps/src/modules/agent-harness/ports/model-access.ts`, token `AGENT_HARNESS_MODEL_PLACEMENT`), Python `ModelPlacementPort` (`backend/python/app/utils/model_placement.py`, default `ALLOW_EVERY_MODEL`), `RunModelPlacement` (`backend/python/app/harness/run_policy/ports.py`, default `NO_RUN_MODEL_PLACEMENT`) and `ModelRegions` (`backend/python/harness_core/models/placement.py`, default `NO_MODEL_REGIONS`). Unbound, every model call is allowed and nothing is refused.
  - Health checks an implementation adds to the Services page: `backend/nodejs/apps/src/modules/agent-harness/ops/service-health-checks.ts` (none in this repository).

The `region` field on every stored harness object and on a worker's claim, and the sandbox label `pipeshub.region`, are shared fields.

## As built

Audited at `959c3bda72`. Decision rows: REPORT D27, D28; the implementation plan D33; P3.2 workspace store; E7 erasure and retention.

- Sealing with the org key plus a per-session workspace key: `backend/python/harness_core/workspace/sealing.py`. Per-org retention at `/services/agentHarness/sessionRetention` (session-retention-settings.ts (moved out of this repository)).
- Passing in the gate:
  - `backend/python/tests/unit/harness_core/workspace/test_sealing.py` (9)
  - `backend/nodejs/apps/tests/modules/agent-harness/session-erasure.test.ts` "ER-3: the workspace key is shredded when the session ran, and left alone when it never did" and "ER-11: audit appends one session.erased with the counts"

### WP-23 (branch `harness/v2-wp23-writeid`)

- **Region.** On every stored harness object: every agent-harness Mongoose model registers through `harnessModel`, which stamps the process's `region` on every insert (create, insertMany, upserts, bulk writes) and never moves it. A worker names its region on every claim and is handed only its region's runs (a run from before regions is the Node's region's); the sandbox broker serves only callers whose sandbox labels name its region (`pipeshub.region`, refused `region_mismatch`). With no extension point every process is in `default`.
- **Snapshot TTL.** `snapshots.ttlDays` is a `min` ceiling: 7 days in the release, which the deployment or the org may shorten (never lengthen; a cache kept longer buys little and holds private derived data). Every `engine.snapshotted` is recorded in `agentSandboxSnapshots` in its append's transaction (the snapshot index, `backend/nodejs/apps/src/libs/repositories/agent-harness/sandbox-snapshots/`); the retention sweeper deletes each org's snapshots past its TTL (object first, then its row); admission never carries an older snapshot into a child, and the worker never restores one older than the run's pinned TTL, whether or not the sweeper ran.
- **Retention default.** No expiry, configurable per org.
- Tests: `backend/nodejs/apps/tests/modules/agent-harness/wp23-region.test.ts` (RES-1, RES-3, RES-4), `wp23-region.mongo.itest.ts` (RES-9, RES-10, the snapshot index contract on Mongo), `wp23-snapshot-ttl.test.ts` (SNT-5..SNT-9), `lcfix-engine-carry.test.ts` (SNT-4), `test_wp23_snapshot_ttl.py` (SNT-1..SNT-3), `backend/python/tests/unit/harness_core/sandbox/test_wp23_region_placement.py` (RES-5..RES-8), `session-retention.test.ts` (RTN-3), `test_c4_10_config_surface.py`.
- The extension points' defaults: `backend/nodejs/apps/tests/modules/agent-harness/harness-edition.test.ts` (the region default; no run placement and no model placement source bound), `backend/python/tests/unit/harness/policy/test_placement_ports.py` (RSP-1..RSP-5), `backend/nodejs/apps/tests/modules/agent-harness/ch07-dependency-health.test.ts` (an added service check), `backend/nodejs/apps/tests/modules/agent-harness/model-access-refusals.test.ts` (the model region default).

## Deviations

1. Placement is one region per process; anything more is the run placement extension point's, with no default in this repository.
2. Model-call placement is not part of WP-23. WP-58 and WP-60 put every model route and every model client through one funnel that asks the placement extension point (C3.68); with none, every call is allowed.
3. A data key's id (`kid`) is unique within its org, not across orgs (WP114-FU-ERASE, the `DataKeyStore` contract's DK-3). A global key table on PostgreSQL would need a cross-org function past the row policies (ADR-SEC-02 keeps none) to buy nothing: ids are random UUIDs (`OrgDataKeys.newKid`), every read and seal count names the org with the id (`list(orgId)`, `reserveSeals(orgId, kid)`), and the KEK binds each wrapped key to its org, id and version (`DataKeyContext`), so another org's id can never open this org's key. DK-3 now asserts that on every store (in memory, MongoDB, PostgreSQL): a reused id is refused within the org, and another org's same id never reaches it. MongoDB still refuses the second org's id (it keys the row by id); PostgreSQL accepts it as that org's own key.

## Owner amendments

- **2026-10-29 (snapshot TTL):** the coding sandbox cache (snapshot) TTL can only be shortened: at most 7 days, default 7. The deployment or the org sets a shorter one on the AI Access page's "Coding sandbox caches" card; a longer one is a release change.
- **2026-10-29 (admin UI):** cards on the existing Workspace › AI Access page (not a new page): "Who agent messages are from" and "Coding sandbox caches". A setting the deployment fixed shows a lock (`locked` on the policy section read). Cards an implementation adds come through `AiAccessExtras` in `frontend/config.ts` (none in this repository).
- **2026-10-29:** the session retention default is no expiry (configurable per org). Confirms ENGINES-PLAN decision 8; OWD-12 keeps retention defaults flexible, so no amendment of the OWD. Related: an org delete erases its data and destroys the key; a user delete erases their own data.

## Status

**Accepted** (2026-10-26). Keys, sealing, retention settings and erasure built and tested; the region on every object, worker and broker placement by region and the snapshot TTL built in WP-23 (2026-10-29); the retention default and the shorten-only TTL decided as built. Run placement and model-call placement are extension points with no default in this repository.
