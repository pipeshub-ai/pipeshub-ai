# Fair scheduling & lanes — rollout

There is **no data migration**: no schema change, no backfill, no message
rewrite. Two steps change broker topology, which is why order matters.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `FAIR_SCHEDULING_ENABLED` | `true` | DRR dispatch + Kafka commit watermark |
| `FAIR_SCHEDULING_KEY_FIELDS` | `orgId,connectorId` | Fairness hierarchy, outermost first |
| `FAIR_SCHEDULING_QUANTUM` | `1` | Messages per key per DRR round |
| `FAIR_SCHEDULING_MAX_BUFFER` | `2000` | Total buffered + parked messages |
| `FAIR_SCHEDULING_MAX_PER_ENTITY` | `500` | Per-leaf-key cap (per connector) |
| `FAIR_SCHEDULING_MAX_DWELL_SECONDS` | `900` | Force-resolve a stuck offset |
| `FAIR_SCHEDULING_LANE_COUNT` | `8` | Broker lanes (1 = laning off) |
| `FAIR_SCHEDULING_LANE_KEY_FIELD` | `connectorId` | Field a lane is chosen from |
| `FAIR_SCHEDULING_LANED_TOPICS` | `record-events` | Topics subject to laning |
| `FAIR_SCHEDULING_PARALLEL_PARTITIONS` | `false` | Several records per Kafka partition at once |
| `FAIR_SCHEDULING_METRICS_PER_CONNECTOR` | `false` | Label dispatch counts by connector as well as org |
| `REDIS_MAX_DELIVERIES` | `10` | Redis dead-letter backstop (deliveries, not failures) |
| `KAFKA_TOPIC_PARTITIONS` (Node) | `1` | Partition count for `record-events` |

## Order

Each step is independently revertable; take them one at a time.

**1. Upgrade.** Scheduling and lanes ship **on**. That is deliberate — the
behaviour they fix is the default one — but enabling scheduling changes the
Kafka commit protocol from `offset+1` per message to a contiguous watermark,
so it is worth knowing about rather than discovering. Set
`FAIR_SCHEDULING_ENABLED=false` to keep the pre-existing FIFO path.

**2. Lanes.** Redis: `FAIR_SCHEDULING_LANE_COUNT` alone. Kafka: set both
`KAFKA_TOPIC_PARTITIONS` (Node applies it) *and* `FAIR_SCHEDULING_LANE_COUNT`
— on Kafka a lane is a partition, so laning without partitions achieves
nothing.

Lanes work independently of the scheduler, and this is a useful place to
pause. On Redis you already get per-key isolation; on Kafka the consumer
gains concurrency equal to the partition count. No dispatch ordering has
changed yet.

**3. `FAIR_SCHEDULING_PARALLEL_PARTITIONS=true`** once fair dispatch is
proven. It is inert without the watermark, so it must come after step 1.

## What to watch

| Metric | Meaning |
|---|---|
| `pipeshub_indexing_watermark_lag{topic,partition}` | **Alert on this.** Offsets read but not committed past. A value that only grows means an offset never reached a terminal state and every later commit on that partition is stalled until a restart replays everything. It should oscillate, not climb. |
| `pipeshub_indexing_scheduler_dwell_exceeded_total` | The escape hatch firing. Should be **zero**. Non-zero means a dispatch path failed to settle its watermark claim and the consumer force-committed past it — that message may not be reprocessed. Investigate. |
| `pipeshub_indexing_scheduler_dispatched_total{org,connector}` | Actual dispatch share — what proves fairness is working. `connector` is `all` unless `FAIR_SCHEDULING_METRICS_PER_CONNECTOR=true`; turn it on for a single-org install, where every connector otherwise collapses into one `org` series and the per-connector share is invisible. Leave it off where the connector count is large or unbounded. |
| `pipeshub_indexing_scheduler_buffer_depth` | Sitting at `FAIR_SCHEDULING_MAX_BUFFER` means the buffer, not the pipeline, is the bottleneck. |
| `pipeshub_indexing_scheduler_active_keys{level}` | Orgs and connectors with buffered work. |
| `pipeshub_indexing_lanes_paused` | Lanes not being read because a key on them is at its cap. Persistently equal to the lane count means one key is starving the rest — raise `MAX_PER_ENTITY` or the lane count. |
| `pipeshub_indexing_scheduler_missing_key_total{field}` | Records with no fairness key, sharing one slice. Usually a payload regression upstream. |

## Tuning

**Fairness is bounded by the read-ahead window.** The consumer can only
reorder records it has already read, so when one connector's backlog sits at
the head of a lane, `FAIR_SCHEDULING_MAX_BUFFER` is what decides how far past
it the consumer can see. If a large existing backlog is starving other
connectors on the same lane, raise the buffer above that backlog's size
rather than the per-entity cap.

**Read-ahead only accumulates when reading outruns indexing.** The buffer
fills at roughly `MESSAGE_BATCH_SIZE_INDEXING` minus the completion rate per
loop. In production indexing takes seconds per record so it fills quickly,
but if fairness ever looks inert, check that ratio before the buffer size.

**Lane collisions are inherent to hashing.** With few connectors and several
lanes, two can share one — three connectors over eight lanes will often use
only two. In-lane hierarchical DRR still separates them, so the effect is
bounded, but raising the lane count reduces the chance.

## Rollback

Steps 1 and 3 are instant: set the flag to `false`. Rolling the scheduler
back leaves lanes alone — they are independent settings.

Lowering `FAIR_SCHEDULING_LANE_COUNT` is safe: the Redis consumer discovers
lane streams that exist and adopts them at startup, so entries on lanes that
drop out of the configured range still drain. A Kafka partition count cannot
be reduced at all — Kafka permits increasing only.

## One-time effects worth knowing

- **Increasing Kafka partitions remaps keys.** For a short window a record's
  events can span old and new partitions. Nothing is lost — the watermark and
  the `record:<id>` lease cover it — but a create and its update for one
  record could reorder. Do it while the topic is quiet.
- **Redis lane streams are self-healing.** The consumer creates each lane and
  its group at the head (`xgroup_create(id="0", mkstream=True)`), so nothing
  published before the group existed is skipped.
- **The pre-lane `record-events` stream stays subscribed** so anything written
  before laning drains normally.
