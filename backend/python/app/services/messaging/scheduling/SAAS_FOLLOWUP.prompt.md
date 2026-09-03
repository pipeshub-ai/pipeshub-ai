# SaaS/EE follow-up prompt — run in the SaaS repo after rebasing onto this work

Paste everything below the line into Claude Code in the SaaS repo, once it has
been rebased on the commit that adds
`backend/python/app/services/messaging/scheduling/` and `.../lanes/`.

Nothing here needs to change in OSS. It exists so the OSS repo records *where
the seams are*, not what is built on them.

---

You are working in the PipesHub **SaaS/EE** repository, which shares
`backend/python/app/services/messaging/` with the open-source repo and has just
been rebased onto the fair-scheduling change.

## What already exists upstream (do not re-implement, do not fork)

- `DRRScheduler[T]` — **hierarchical** Deficit Round Robin over per-key virtual
  queues. Keys are tuples of levels (`("org-7", "connector-42")`); it
  round-robins between orgs and, within each org, between that org's
  connectors. Broker-agnostic, single-threaded, no knowledge of messages.
- `FairnessKeyExtractor` — `extract(message) -> FairnessKey` (a tuple).
- `WeightProvider` — `quantum_for(key) -> int`, consulted **on every dispatch
  turn**, so a key's share can change at runtime without a restart. `key` is
  the prefix of the level being weighted: `("org-7",)` when choosing between
  orgs, `("org-7", "conn-42")` when choosing between that org's connectors.
  Weighting one level does not reweight the other.
- `CompositeKeyExtractor` — one payload field per level, default
  `("orgId", "connectorId")`.
- `PartitionOffsetTracker` — Kafka contiguous commit watermark, with
  `mark_done` / `mark_redeliver` / `mark_dispatched` / `stale_offsets`.
- `FairSchedulerConfig`, including `parallel_partitions` (per-record rather
  than per-partition serialisation on Kafka).
- `lanes/` — `LaneRouter`, `KafkaLaneRouter`/`RedisLaneRouter` (stable SHA-256
  hash, never `hash()`), and `LaneAwareProducer`, a producer decorator that
  routes every publish site without editing any of them.
- `MessagingFactory.create_consumer(..., fair_scheduler_config=,
  key_extractor=, weight_provider=)` and `create_producer(..., lane_config=)`
  — the injection points.

**Important context:** in this codebase `orgId` *is* the tenant. There is no
separate `tenantId` and you should not introduce one. So the multi-tenant
fairness key is the same key OSS already uses. The SaaS delta is **not** the
key — it is policy: who gets what share, and who may enqueue at all.

## What to build here

### 1. Tier-weighted `WeightProvider`

Back it with the tenant/plan tier service.

- Free/trial orgs get quantum 1; paid tiers more. Read the tier from whatever
  plan store exists here — do not add a new one.
- Cache tier lookups aggressively (seconds, not per-call): `quantum_for` is on
  the dispatch hot path, called once per key per DRR round.
- Never block in `quantum_for`. It is synchronous and runs on the consumer's
  main event loop. Refresh out of band.
- An unknown key must return the default, never raise — a lookup failure must
  not stall dispatch.

### 2. ~~Composite key extractor~~ — already upstream, nothing to do

Two-level fairness (`orgId` then `connectorId`) landed in OSS, because the
single-org install needs it just as much: without the second level every user
in an org shares one queue. Do **not** reimplement it. For a third level or a
different order, set `FAIR_SCHEDULING_KEY_FIELDS` — no code.

### 3. Per-tenant admission control (the actual root fix)

Consumer-side reordering cannot fix an unbounded producer. Add per-org
admission control on the **producer** side, in the connector sync path
(`data_source_entities_processor.py` publishes batches of 50–100 with no
throttle whatsoever).

- A Redis-backed token bucket or in-flight cap keyed by `orgId`, so the limit
  holds across connector replicas.
- Free tiers get a low ceiling; paid tiers higher.
- Must degrade open: if Redis is unavailable, publish anyway. Never let the
  quota store become a hard dependency of ingestion.

### 4. Tenant-aware lane routing

Implement a shuffle-sharding `LaneRouter`: map each org to a small
random-but-stable *subset* of lanes rather than one lane, so two noisy orgs
are unlikely to collide on every lane they share. Flat hashing collides —
three connectors over eight lanes will often use only two — and in-lane DRR
is the only thing bounding the damage today.

Note the protocol is producer-side only: `route()` and `lane_topics()`. Do not
add a consumer-side `lane_for()`; a message's lane is intrinsic to where it
arrived (Kafka partition / Redis stream name), and recomputing it locally
would be a second source of truth that diverges from the broker's own
partitioner.

**The lane hash must stay in step with the Node producer.** OSS uses SHA-256
precisely because Node cannot produce BLAKE2b at an 8-byte digest; pinned
cross-language vectors are asserted in both `test_lane_router.py` and
`lane.utils.test.ts`. If you change the hash, change all four.

Inject through `MessagingFactory.create_producer(..., lane_config=)` plus your
own router; do not edit the OSS `hash_router.py`.

### 5. Per-tenant metering

Emit `pipeshub_indexing_scheduler_dispatched_total` into whatever
billing/usage pipeline exists here. It is the number that shows a tenant
consuming more indexing capacity than their tier pays for, and the input to
any future quota enforcement. Note `FAIR_SCHEDULING_METRICS_PER_CONNECTOR`
controls whether the connector label is populated — leave it off here unless
the connector count is bounded, since it multiplies the series count.

## Rules

- **Do not modify anything under `app/services/messaging/scheduling/` or
  `app/services/messaging/lanes/`.** They are shared with OSS. If you need a
  hook that does not exist, open an issue against the OSS repo describing the
  seam; do not patch it here and create a rebase conflict.
- Everything you build goes in this repo's own module tree, wired in through
  the factory arguments above.
- If a wiring point is unreachable because `indexing_main.py` calls the factory
  with no overrides, add the env-driven resolver
  (`FAIR_SCHEDULING_PROVIDER=pkg.mod:factory` returning a composition object)
  **to the OSS repo** rather than forking `indexing_main.py` here.
- Tests: tier weight changes take effect without a restart; an unknown tier
  falls back to the default; an org with many connectors does not out-compete
  an org with one; admission control degrades open when Redis is down.
