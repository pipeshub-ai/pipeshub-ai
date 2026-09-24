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
   search on the `entities` collection. There is no score threshold.
3. **Merge decision.** One structured model call per record (role `indexing`,
   low reasoning effort) answers, pair by pair, whether a name is the same
   concept as its winner, the same concept as another name in the record, or
   new. Every answer is validated: a target must be the offered winner, an
   in-record pointer must be of the same kind, anything else becomes new.

Languages go through a static ISO table and skip tiers 2 and 3. Departments
keep their exact match against the org's department list. Record and
record-group entities are identities and are never merged.

## What gets written

- Canonical nodes carry `name` (first spelling wins, never renamed),
  `normalizedName`, `orgId`, `aliases` with their `normalizedAliases`
  (capped at 20) and a deterministic
  `_key` derived from `(orgId, collection, normalizedName)`, so two records
  that create the same name concurrently converge on one node.
- Every `belongsTo*` edge carries `extractedName`, the raw string the model
  produced for that record. A wrong merge can be undone per record from it.
- The entity vector point for a node keeps its id and is embedded from the
  canonical name only, so the vector never drifts as merges accumulate.
  Aliases are payload only: shown to the merge model and in
  `search_entities` results, never embedded. A point is rewritten only when
  its payload or membership changed.

Subcategories only resolve within their own level, and per-org nodes never
link across orgs. Legacy global nodes created before the feature are not
migrated; a reindex moves a record onto canonical nodes.

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
| Model unavailable or malformed | Every name in that call becomes new; `model_error` counter |
| Model returns an id it was not offered | That name becomes new; `rejected_target` counter |
| Graph lookup fails | Apply mode: enrichment fails as today, no point is written. Shadow mode: logged |

Metrics live in `app/telemetry/modules/entity_resolution_metrics.py`:
names by outcome, model calls by result, fallbacks by reason and per-record
latency. Each record also logs one `entity_resolution mode=... tier0_hits=...`
line.

## Operational notes

- The `entities` vector collection gains a `metadata.level` payload index.
  It is created with the collection, so on a dev box that already has the
  collection, delete it once and let it be recreated, or level-filtered winner
  lookups will silently return nothing on Redis.
- The graph indexes on `(orgId, normalizedName)` and, on Arango,
  `(orgId, normalizedAliases[*])` are created by the providers' index setup
  on startup for `categories`, `subcategories1..3`, `topics` and
  `languages`. Neo4j alias matches scan the org's nodes of a label through
  a single `orgId` index.
