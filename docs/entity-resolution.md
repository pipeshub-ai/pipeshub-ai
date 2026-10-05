# Taxonomy entity resolution

Extraction returns free-text categories, subcategories and topics. Without
resolution every spelling ("bug bash testing", "Bug bash testing", "bug bash
testing session") becomes its own graph node and its own point in the
`entities` vector collection, and the knowledge-graph tools then show three
entities for one concept. The resolver in
`backend/python/app/modules/entity_resolution/` maps each extracted name to one
canonical, per-org node before anything is written.

## Where it runs

Indexing, between classification and every write that consumes the names:

```text
classify -> resolve_entities -> record summary -> blob -> enrich (graph + entity points)
```

Both the service path (`app/events/events.py`) and the legacy pipeline
(`app/modules/transformers/pipeline.py`) call
`SinkOrchestrator.resolve_entities(ctx)`. The resolver mutates
`record.semantic_metadata` to canonical names and attaches an
`EntityResolution` to the transform context; `GraphDBTransformer` reads it to
pick the nodes.

## The three tiers

1. **Exact match.** Names are normalized (NFKC, casefold, collapsed whitespace,
   surrounding quotes and trailing punctuation stripped) and looked up in one
   indexed query per collection against `normalizedName` and
   `normalizedAliases`, so a spelling that was merged once resolves exactly
   from then on, with no vector query and no model call.
2. **Winner lookup.** Every miss gets its single nearest existing entity of the
   same org, entity type and subcategory level, from a hybrid dense + BM25
   search on the `entities` collection. There is no score threshold. Winners
   are then checked against the graph in one batched call per kind: a point
   whose node was deleted, belongs to another org, or is a legacy global node
   (no `normalizedName`) is not offered (`stale_winner`).
3. **Merge decision.** One structured model call per record (role `indexing`,
   low reasoning effort) answers, pair by pair, whether a name is the same
   concept as its winner, the same concept as another name in the record, or
   new. Every answer is validated: a target must be the offered winner, an
   in-record pointer must be of the same kind, anything else becomes new. A
   cleaned display form for a new name (`canonical_name`) is kept only when it
   differs from the extracted name in case, whitespace or punctuation alone
   (`normalizer.spelling_key`); one that adds or drops words is ignored
   (`rejected_canonical`), since it would otherwise merge into an existing node
   the model was never offered. Combining marks, digits and symbols count as
   spelling ("दिन" is not "दीन", "C++" is not "C#"), and so does punctuation
   inside a number or at the start of a name, including after an opening
   bracket or quote ("3.11" is not "311", ".NET" and "(.NET)" are not "NET").

Languages go through a static ISO table and skip tiers 2 and 3. Departments
keep their exact match against the org's department list. Record and
record-group entities are identities and are never merged.

## What gets written

- Canonical nodes carry `name` (first spelling wins, never renamed),
  `normalizedName`, `orgId`, `aliases` with their `normalizedAliases`
  (capped at 20) and a deterministic
  `_key` derived from `(orgId, collection, normalizedName)`, so two records
  that create the same name concurrently converge on one node. New nodes and
  new aliases are written before the record's graph transaction opens: both
  writes are idempotent, and inside two open ArangoDB stream transactions two
  inserts of the same key conflict and fail one record's enrichment.
- Every `belongsTo*` edge the resolver writes carries `extractedName`, the raw
  string the model produced for that record. A wrong merge can be undone per
  record from it. Edges copied onto a deduplicated record by
  `copy_document_relationships` carry only `createdAtTimestamp`.
- The entity vector point for a node keeps its id and is embedded from the
  canonical name only, so the vector never drifts as merges accumulate.
  Aliases are payload only: shown to the merge model and in
  `search_entities` results, never embedded. A point is rewritten only when
  its payload or membership changed.

Aliases are written only to a node of the writing org: a legacy node (no
`orgId`) or another org's node is left unchanged, so one tenant's spellings
never reach another's entity search. A deduplicated record is projected
from the nodes its copied edges reach. Its own org's nodes keep their
aliases, nodes without an org (legacy, or global departments) are projected
without aliases, and another org's node is skipped.

Subcategories only resolve within their own level, and per-org nodes never
link across orgs. Legacy global nodes created before the feature are not
migrated automatically; a reindex moves a record onto canonical nodes, and an
operator can migrate them (see Consolidation).

## Consolidation

Nodes that should have been one (created while the model was unavailable, or
by two instances at once) stay separate until merged. Legacy nodes stay
shared until migrated. Both are operator commands, dry runs unless `--apply`
is given:

```
python -m app.scripts.kg_taxonomy duplicates --org ORG
python -m app.scripts.kg_taxonomy consolidate --org ORG [--collection topics] [--apply]
python -m app.scripts.kg_taxonomy merge|unmerge ...
python -m app.scripts.kg_taxonomy legacy --org ORG
python -m app.scripts.kg_taxonomy migrate-legacy --org ORG [--apply]
python -m app.scripts.kg_taxonomy unmigrate-legacy ...
```

- `consolidate` only merges nodes of one org and collection whose names share
  a spelling key: they differ in case, spacing or punctuation, never in words.
  The oldest node wins.
- A merge is a redirect, not a delete:
  - the loser keeps its data and gets `mergedInto` and `mergedAt`;
  - its record edges move to the winner with `extractedName` kept and
    `mergedFrom` set;
  - the winner learns the loser's spellings as aliases.
- Lookups, the entity index and the stale-point sweep skip merged nodes.
- A newly extracted name whose deterministic key is a merged node resolves
  to the node it was merged into. Otherwise the record would link back to the
  hidden node whenever the winner's alias list is full.
- Merges record `mergedFrom` on the edges they move and migrations record
  `migratedFrom`, so undoing one never hides the other's edges. An edge keeps
  the origin of its first move, and a merge re-points older redirects at the
  new winner. Chained merges (A into B, then B into C) therefore undo one node
  at a time, and undo follows redirects to wherever the edges are now.
- `unmerge` moves the marked edges back.
- `migrate-legacy` moves one org's edges from a legacy node onto that org's
  canonical node for the same name, created if absent. Other orgs keep the
  legacy node.
- Entity points are refreshed as each change is made. If that fails, the
  command reports `index_refreshed: false` and exits 1. The background sweep
  repairs live org nodes but skips merged and legacy ones, so re-run the merge
  or the unmigrate.
- Edges only move onto a node of the same org; undoing a migration is the one
  move allowed back onto a legacy node.
- Finding legacy nodes walks the legacy nodes in key order and counts each
  one's records of the org, so run it off-peak on large installs. A move reads
  and moves its edges 5,000 at a time.
- Dry runs only read, so they work with a read-only graph user; `--apply`
  first applies the graph schema.
- Exit codes: 0 done; 1 some items failed or left the index unrefreshed; 2
  invalid request; 3 a single-item command failed, or the graph or its schema
  was unavailable before anything was written. Bulk commands carry on past a
  failed item or collection, print it with `error`, and exit 1; each item is
  idempotent, so a re-run finishes it.
- Known limits:
  - `unmerge` restores only edges that moved. When a record linked to both
    nodes, the loser's edge (and its `extractedName`) is dropped rather than
    duplicated, so it is not recreated.
  - Undoing the middle of a chain (B in A into B into C) leaves A redirecting
    to C.
  - A loser spelling that did not fit in the winner's alias list can still
    become a new node when a later record extracts it, unless it is the
    loser's own name.
  - Aliases the winner learned are kept after `unmerge`. On Neo4j, the alias
    nodes then point at both nodes.
  - Indexing that resolved to the loser just before the merge can link to it
    after the merge finished; re-running the merge moves those edges.
  - Category hierarchy edges are not moved; nothing reads them today.

## Entity index rebuild

The `entities` collection is a projection of the graph. The indexing service
rebuilds it in the background (`app/modules/indexing/entity_index_rebuild.py`),
without extraction or model calls other than embedding:

- **Per connector** (app document): its record groups, then its indexed,
  non-deleted, named records, written as on the index path.
- **Per org** (org document): its canonical taxonomy nodes and its
  departments (including global ones). Membership is read from the graph and
  replaces the stored one; a node no record reaches has its point deleted.
- **Sweep, per org every 24 hours:** the org's taxonomy, department and
  record-group points are checked against the graph in batches. A point is
  deleted when its node is gone or belongs to another org. Points of legacy
  nodes without an org are kept, because records still link to them. Record
  points are not swept; single-record delete removes them.

A document is done when its `entityIndexState` equals
`v<ENTITY_INDEX_VERSION>:<provider>:<model>:<dimension>`, so changing the
embedding model re-runs every pass. Each point also records the model that
embedded it (`metadata.embeddingModel`). A write re-embeds a point from
another model, or one written before this field existed, even when its text
is unchanged. Indexing therefore repairs whatever a pass missed. The first
rebuild after an upgrade re-embeds every entity point once.

Every service's entity store follows a model change without a restart. It
checks the embedding config on each call against `ConfigurationService`'s
cache, which the change notification clears, and reads the stored config at
least once a minute in case a notification is missed. A config that cannot be
read keeps the current model. On a change the store rebuilds its client, so
the next write, search and rebuild tick use the new model and the marker
moves.

Writes are also checked against the stored config, not only the cache: just
before it upserts, each written batch re-reads the config from the key-value
store, as the records path does per record. A batch embedded with a model
the stored config no longer names is refused, and so is one whose model
changed in this process while it was embedding. This covers an indexing
replica that missed the notification and still holds the old model while
another has already recreated the collection. The passes write a refused
entity again.

A store checks the collection against its new model: the collection's
dimension, and the model recorded on one stored point. The collection does
not match when the dimension differs, or when the dimension is the same but
that point was embedded by another model. At the same dimension, the old
vectors would otherwise answer new-model queries with no error. Points from
before `metadata.embeddingModel` existed are not counted as a mismatch; they
are re-embedded in place.

- Only the rebuild leader drops and recreates a collection that does not
  match, at the start of a tick while it holds `entity_index_rebuild:leader`.
  The passes then refill it. Two replicas dropping in turn would lose the
  points the first one had refilled.
- While the rebuild loop waits between ticks, it checks the configured model
  every 5 seconds (a cache read) and ends the wait on a change. So the leader
  recreates within seconds of the switch, even when nothing is being indexed.
- Every other store fails entity calls with the mismatch (`The indexing
  service recreates it`) and retries initialisation every 30 seconds. That
  includes the query and connector services and the other indexing replicas.
  After a restart that finds a mismatched collection, entity writes fail
  until the leader's first tick, which comes after a 60-second startup grace.
- If the stored point cannot be read, the switch fails and is retried 30
  seconds later. The store does not adopt the new model on an unread
  collection.

Points of legacy nodes without an org are not projected, so after a recreate
they return only when their records are reindexed.

The rebuild runs on one indexing replica at a time (Redis leader
`entity_index_rebuild:leader`), one page per tick. It resumes from the cursor
on the document after a restart. The cursor is valid only for the marker in
`entityIndexTarget`; under a new marker the pass starts over.

- A pass with write failures is retried from the start twice.
- After that, the document is marked done with `entityIndexExhausted: true`
  and its failure count kept.
- A document whose pages raise on 10 consecutive ticks is also given up, so
  it cannot hold the loop.
- A sweep that fails 3 times waits for the next interval. On the Redis vector
  backend, a sweep cannot page past 10,000 points of one org and stops there.

Progress is logged under `entity_index_rebuild:` with the app or org key,
phase and cursor.

## Modes

Resolution is always on: the indexing container builds the resolver in
`APPLY` mode and there is no feature flag. `ResolutionMode.SHADOW` (compute and
log every decision, write nothing) and `ResolutionMode.OFF` exist as
constructor options for tests and for anyone wiring the resolver by hand.
Shadow decisions are logged as `entity_resolution shadow ... decisions=[...]`.

## Failure policy

| Failure | Behaviour |
| --- | --- |
| Vector store unavailable | Names become new nodes; `vector_error` fallback counter |
| Model unavailable or malformed | Every name in that call becomes new; `model_error` counter; the cached model is rebuilt from config for the next record |
| Model call slower than 60 s | Same as unavailable. The bound applies to each provider call (and each reflection retry), not to the wait for the shared indexing model slot, which is backpressure |
| Model returns an id it was not offered | That name becomes new; `rejected_target` counter |
| Winner check against the graph fails | No winner offered for that kind; `winner_check_error` counter |
| Graph lookup fails | Apply mode: enrichment fails as today, no point is written. Shadow mode: logged |

Metrics live in `app/telemetry/modules/entity_resolution_metrics.py`:
names by outcome, model calls by result, fallbacks by reason and per-record
latency. Each record also logs one `entity_resolution mode=... tier0_hits=...`
line.

## Operational notes

- Entity index writes are counted in
  `pipeshub_entity_index_writes_total{operation,outcome}` (written,
  membership_only, unchanged, skipped, failed). The ids of entities not
  written are logged at warning, capped at 20 plus a count. A rising
  `failed` count means the vector store is refusing entity writes; the
  rebuild repairs the points once it recovers.
- Deleting a connector or a KB publishes `deleteConnectorEntities`, and the
  indexing service removes the connector's entity points:
  - shared taxonomy points lose the connector and its record groups, and the
    rest are deleted;
  - a failure is retried, then dead-lettered;
  - before any graph row goes, the deleting service records the cleanup in
    the KV store (`/services/entityCleanup/pending/<connectorId>`) and does not
    delete if it cannot; the event's handler clears it when the cleanup
    finishes. The rebuild loop reads the intents every 5 minutes and runs any
    older than 15 minutes (a lost publish, a dead-lettered message) once the
    connector's app document is gone, backing off on failure; a failed read
    of the app document never counts as gone. An intent whose app still
    exists a day later (its delete was reverted) is dropped;
  - each page is re-read by id and written by id, so a record indexed on the
    same indexing instance during the cleanup keeps its connector (the locks
    are per process; another instance's write can still be lost until that
    record is reindexed or the rebuild repairs the point);
  - deploy the indexing service before the connector service: an older
    indexing service dead-letters `deleteConnectorEntities`, and those
    messages then need replaying.
- Embedding runs before the per-entity locks are taken. The locks only cover
  the read, merge and write, so records sharing a popular entity do not wait
  on each other's embedding call.
- The `entities` vector collection has a `metadata.level` payload index. The
  store ensures its payload indexes on every start (index creation is
  idempotent on every backend), so an existing collection picks it up too.
- The graph indexes on `(orgId, normalizedName)` and, on Arango,
  `(orgId, normalizedAliases[*])` are created by the providers' index setup
  on startup for `categories`, `subcategories1..3`, `topics` and
  `languages`. On Neo4j each stored alias is also a
  `(:TaxonomyAlias {orgId, collection, normalized})-[:ALIAS_OF]->(node)`
  node under a composite uniqueness constraint, since a list property cannot
  be index-seeked; alias matches seek those instead of scanning the org's
  nodes of a label.
