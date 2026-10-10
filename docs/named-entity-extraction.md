# Named entity extraction

Indexing can extract typed entities from a record and store them beside the
taxonomy graph. The Labs flag `ENABLE_NAMED_ENTITY_EXTRACTION` is off by
default. Turning it off does not delete nodes other records still mention;
it removes the mention edges for records indexed while the flag is off.

## What is stored

Entities live in the `namedEntities` collection (`NamedEntity` on Neo4j).
A record points at them with `mentionsEntity` (`MENTIONS_ENTITY`). These are
not `person`, `users`, or `organizations` nodes. A person mention requires
no email and is never written with `upsert_person_by_email`.

Typed values (dates, date ranges, date-times, durations, amounts,
percentages, ages, dimensions) are not entity nodes. Each is a row of the
record that mentions it, in `valueMentions` (`ValueMention` on Neo4j), keyed
by record, kind and normalized value. A shared node for a value such as
`2026` or `USD` would be a hub that every record write locks, and a range
filter over value nodes cannot say "this record mentions both". A value row
carries the same mention statistics as a mention edge (`mentionCount`,
`blockIndexes`, `blockIds`, extractors). Value rows have no id a client may
use: `entityFilters.entityIds` takes entity node ids only.

Value kinds carry a typed payload so a filter can use a range:

- dates are half-open epoch milliseconds (`startMs` inclusive, `endMs` exclusive)
- money is a decimal amount plus an ISO-4217 code
- quantities are stored in SI units
- percentages are fractions (`300 bps` is `0.03`)

A date with no year is marked ambiguous and has no range. A period covers
the whole period: `Q3 2026`, `FY2025`, `next week` (Monday to Monday),
`last month`, and `this year` are ranges anchored on the record's reference
time. Percent words (`percent`, `pct`, `bps`) and currency words (`dollars`,
`euros`, `yen`, `rupees`) are values too. `pounds` is not, because it is
also a weight.

Card numbers (Luhn-checked), IBANs (mod-97-checked), and national IDs (US
SSN, UK NINO, Aadhaar, PAN) are detected only so they are dropped. That
holds for model output as well: a card number submitted as a product is
rejected before it reaches the graph.

An entity node's key is `uuid5(orgId:kind:normKey)` in namespace
`c3a1e7b2-4d58-4f0a-9b6e-2a7d8c1f0e55`. Identity is the norm key. Two kinds
never share a node, and two orgs never share a node. A value row's key is
`uuid5(value:recordId:kind:normKey)` in the same namespace, so each record has
its own row. Numeric keys are canonical: `$1,250` and `USD 1,250.00` are both
`money:USD:1250`, so a record that states both has one row for them and an
`amount` filter matches either wording. A money value is not an entity node and
cannot be an `entityFilters.entityIds` target.

## How a record is extracted

The indexing pipeline runs extraction at the same time as document
classification. Its status is stored with the extraction in the record's blob
and does not change `indexingStatus`.

The deterministic recognizers (email, URL, dates, amounts, quantities) run
first on every record. The model then adds what they cannot find, picked in
this order:

1. A short read-only agent (`app/agent_loop_lib`) with per-record tools:
   read blocks, search the document, normalize a value, submit entities,
   and finish. The run is capped at 8 turns, 24 tool calls, and 120 seconds.
   Accepted entities are kept when a budget trips.
2. One structured call, when the model cannot call tools, the index LLM
   pool is full, or the agent fails before it accepts anything.
   It asks for a strict schema, then a non-strict tool call, then prompted
   JSON. A model starts at a lower mode only after that mode answered a prompt
   a higher one failed on, and only for an hour: an error every mode shares (a
   content filter, an outage, a 429) changes nothing.
3. The deterministic results alone, when no semantic kind is enabled or
   the structured call also fails.

The model fills a flat JSON object: `text`, `block`, `kind`, `normalized`.
Typed values are built in code after the span is grounded in the block, so
a name the model invents is dropped. The value is always parsed from the
grounded text; `normalized` is advisory and never sets it, because a hint can
name an amount or date the document does not state. A date, amount or quantity
whose text states none (`"the kickoff"` as a date) is rejected, and so is one
that parses to another kind's value (`"3 days"` as a date); the agent is told
why. Each kind declares the value types it accepts (`value_types` on its spec).

Grounding takes the first match that is a whole word: `Ed` is not inside
`Edward`, and `$5` is not inside `$50`. Only semantic names may ground fuzzily
(a near-miss spelling); a value never does, since `$12,500` is not `$12,600`.
Fuzzy work is capped per run (surfaces of up to 10 words, 200 fuzzy attempts,
50 "nearest" hints), and staging runs off the event loop so a long model reply
cannot stall other records or their lease heartbeats. A span longer than 2,048
characters is not an entity; a key longer than 512 bytes is stored as
`<scheme>:sha256:<hex>`, so it always fits a graph index. Mention offsets index
the block text as stored, leading whitespace included.

Amounts read word scales (`5 million`, `₹5 crore`, `$2.5 Bn`), signs (`-$500`,
`($1,200)`, `-3%`) and the `HK$`, `NZ$`, `S$`, `CN¥`, `RMB` and `Rs.` symbols.
A plain space groups thousands only before a trailing currency (`1 250 €`), so
`$5 100 times` is five dollars. `ALL`, `CUP`, `SOS` and `TOP` are not read as
currency codes before a number, and an amount followed by letters (`CAD 3D`) is
not an amount. Fiscal periods (`Q3 FY25`) are still read as calendar periods.

The status is `COMPLETED`, `PARTIAL` (a budget tripped, the model failed
after some entities were accepted, or part of the document was never sent to
the model: windows past `max_llm_windows`, a failed window, or text past the
1,000,000-character cap), `SKIPPED` (no text), or `FAILED`. The stats record
how many windows there were, ran and failed, and whether text was cut.

A graph write that still fails after its own deadlock retries never fails the
document's indexing. The record keeps its last good entities, and a retry keyed
by the record goes into `namedEntityPersistRetries` (`NamedEntityPersistRetry`
on Neo4j). The stale-recovery pass, under its lock, writes due retries again
from the extraction stored in the record's blob, with no model call. Each
failure doubles the wait (one minute, up to six hours); after eight the retry
is dropped with an error log, and the record is written again when it is next
indexed. A read that fails (record or blob) counts as a failure; only a record
that is gone, or a blob read that holds no extraction, ends a retry early.

A retry never puts an older extraction over a newer one. It holds the record's
lease (`record:<id>`, held by the indexing consumer while it processes the
record) from the blob read to the end of the write, skips a record that is
being indexed, and bounds its write under the time a delivery waits for that
lease. Every write also claims the retry marker before it writes anything: a
normal write drops it, and a retry's write goes on only while its marker is
still the one it read. On ArangoDB the marker, mention edges and value rows are
one transaction. Where the claim commits on its own (Neo4j without explicit
transactions), a retry that claimed its marker and then hit a deadlock keeps
its claim for the next attempt rather than reading the missing marker as a
newer write, and a retry that fails again carries its failure count.

A `FAILED` run keeps the record's existing mention edges, because a transient
model error must not erase the last good extraction. So does a run ended by a
model error that left only deterministic entities, when the record already has
entities; a record with none gets the deterministic ones.

The extraction service caps concurrent runs at
`MAX_CONCURRENT_ENTITY_EXTRACTIONS` (default 8) per process, so N replicas run
up to N × 8. A request waits up to 10 seconds for a slot, then gets a 429 with
`Retry-After: 5`. The caller treats that as backpressure: it retries on its own
budget and pauses its consumer, and it never counts against the circuit breaker
that classification shares. A record that is still shed is `FAILED` until its
next reindex.

## Resolution

Semantic names are resolved in shadow mode. A possible merge is recorded
and is not applied, so the graph still gets a new node. Value kinds are
keyed only by their norm key. Organization keys drop legal suffixes, so
`Globex Corporation` and `Globex Corp.` share a node without resolution.
Production stays in shadow; apply mode is scored only by the offline eval
below.

A recorded merge (`mergedInto` on the loser) is followed in every mode: a name
whose own node was merged links to the survivor, and an `entityIds` filter on a
merged id also matches the survivor's records. Redirects are followed up to 16
hops within the org and stop at a cycle.

## Retrieval

Search accepts `filters.entityFilters` (kinds, name, entity ids, a date
range, an amount, a quantity). The gateway keeps that object; retrieval
reads it before other filter keys are lowercased. The name filter is a
case-insensitive prefix.

Every constraint must hold for the **record**, not for one entity: an amount
and a date match a record that mentions both. A name or entity ids match
entity nodes; a date, amount, quantity or percentage matches value rows. A
kind of the typed constraint's own kind narrows it (`currency` with an amount,
`date_range` with a date); any other kind is a constraint of its own, "the
record mentions an entity of one of these kinds". Each constraint is matched
on its own and the record sets are intersected. A constraint too broad to
list is checked only within the narrowest constraint that could be listed,
so a broad date range still works next to a selective name; only when every
constraint is too broad is the filter reported as too broad.

The match runs first and grants nothing. It is one capped graph lookup
that returns the mentioning records' virtual record ids, and those ids
narrow the search the same way a tool's id list does. The normal
permission path still decides what the user may read: with
`ENABLE_CONTAINER_PERMISSION_FILTER` on, the search keeps its container
scope and adjudicates the hits. An entity filter never loads the user's
whole accessible-record map to intersect a few thousand matches. A filter
that matches more entities than the cap is reported as truncated and not
searched, rather than searched on an arbitrary subset. A failed lookup is
an error, never "no match".

`find_records_by_value` keeps the hits in the agent's scope (its apps and
knowledge bases, `strictScope`, demo-app exclusions, through
`load_entity_access_context`). It then permission-checks them in
batches with `filter_accessible_record_ids`. It returns at most 50
records and rejects a larger readable set, asking for a narrower filter,
instead of returning a partial list; the check stops at the 51st readable
record. If scope or permissions can't be resolved, the tool call fails
rather than reporting an empty result.

Agent tools, when the flag is on:

- `search_entities` accepts `named_entity_kinds`
- `find_records_by_entity` accepts a named-entity id
- `search` can scope by those ids
- `find_records_by_value` filters by a typed value
- `fetch_record` lists entities on records it already returned

An entity is visible only when the caller can read a record that mentions it.
Records in the trash never come back from an entity filter; the entities on
a trashed record are still readable from the record itself.

In chat, the request's `entityFilters` scope every content search of the turn:
the search behind an internal-search answer, and the agent's retrieval and
knowledge-graph searches. Browsing (`navigate`, `lookup_record`,
`fetch_record`) is not filtered, as with app filters. A filter that is refused
(too broad for what the user can read) is not "nothing found": the answer, or
the agent's tool result, says why and how to narrow it.

## Configuration

`/services/namedEntities` overrides the timezone (default UTC), enabled
kinds, and budgets. Phone numbers and IP addresses are off unless enabled
there.

## Evaluation

The extraction-quality eval set (labelled spans and values, resolution clusters,
identity spellings, the agent-vs-single-call gate and their thresholds) is
maintained in the enterprise edition, not in this repository. The open-source
suite keeps the behaviour tests: the normalizers, grounding, the agent and the
single call over scripted replies, and `test_identity_golden.py`, which fails on
any drift in a node key.

## Rollback and compatibility

The ArangoDB `records` schema is strict and every start reapplies the schema
of the running build. A record that carries `entityExtractionStatus`,
`lastEntityExtractionTimestamp`, `entityExtractionStrategy` or
`entityExtractorVersion` cannot be updated by a build that predates those
fields. So the change ships in two releases. This one adds the fields to the
schema and writes none of them; the status lives in the blob. The next release
writes them, once every build a deployment can roll back to has the schema.
`test_records_this_release_writes_stay_updatable_under_the_previous_schema`
holds this release to that, and `test_no_module_writes_entity_extraction_status`
fails if a writer appears early. A missing status means "not extracted".

`entityExtractionStatus` is a bounded uppercase string in the schema, not an
enum, so a new status needs no schema release. `RECORD_ENTITY_STATUSES` in
`domain/models.py` is the gate for writers.

The `namedEntities`, `mentionsEntity` and `valueMentions` schemas type their
known fields but allow additional ones, as `recordRelations` and
`entityRelations` do. Once this release ships it is the "older build" a later
one can roll back to, and its reapplied schema must not reject documents that
carry a field the later release added.
`test_fields_a_later_release_adds_do_not_block_updates` holds this.

The blob holds the full extraction, so the graph and the entity vectors can
be rebuilt from it without a model. It is written even when classification
gave no semantic metadata (the record's graph still sees none, which it records
as a failed classification), and it records what relative values were resolved
against (`reference_time_ms`, `tz`) and the `key_scheme` the keys were built
with. A vector-only reindex reads it back and writes the record's mentions,
value rows and entity vectors again (`SinkOrchestrator.reproject_named_entities`);
a key or schema migration uses the same path.

## Identity and keys

A node key is `uuid5(orgId:kind:normKey)`. A key never changes silently.
`keyScheme` on each node names the recipe that produced the key. Changing the
recipe is a migration: write the new nodes, set `mergedInto` on the old ones
so the old key keeps resolving, and rebuild the edges from the blobs. Golden
vectors in `test_identity_golden.py` fail on any drift, including drift in the
shared `normalize_name`.

The kind in a key is always a **base kind**. An ontology class (a "Vendor" or
"Customer" view of an organization) is a label in the node's `types` and never
part of the key, so reclassifying an entity does not move its id and one
company is one node whichever source called it what. Built-in kinds never
contain a dot; `x.<org>.<name>` is reserved for org-defined base kinds, so one
can never collide with a built-in added later. `named_entity_key` refuses any
other kind string.

Organization names drop legal forms to a fixed point, with any connector left
before them (`Bain & Co` → `bain`, `Porsche GmbH & Co. KG` → `porsche`,
`Infosys Pvt Ltd` → `infosys`), unless nothing or only an article would remain
(`AG` and `The Limited` stay whole). The forms are Inc, LLC, Ltd, Limited, GmbH,
Corp, Co, Company, AG, SE, PLC, LLP, LP, SA, NV, BV, Pty, Pvt, Private, KK, Oy,
AB, SpA, SAS, SARL, SRL, Sdn Bhd, KG and KGaA. The display name is the same
stripped name, so a node shows the name its key is made of. Curly and straight
apostrophes key the same. An email key is the whole address casefolded: RFC 5321
allows a case-sensitive local part, but no mainstream provider uses one. A URL key is the
lowercased scheme and host, the path, and the sorted query: the query names the
resource (`?v=`, `?id=`), so it stays. Credentials (`user:pass@`, `token`,
`signature` and the other `SENSITIVE_QUERY_PARAMS`), signing and tracking
parameters (`X-Amz-*`, `X-Goog-*`, `utm_*`, `gclid`) and the fragment are
dropped. The graph stores the canonical link as the URL's name and on its edges,
never the raw text.

`mentionsEntity` means "this record mentions this entity" and nothing more.
Roles and facts ("signed by", "effective on") go in a separate edge
collection so the meaning of this one is never overloaded.

## Cleanup

`orphanedAt` marks a node with no mention edges. Nodes still orphaned after
the grace period are deleted together with their vector points. A merged node
has no mentions by design and is never swept: it holds the redirect that its old
key and stored ids follow. Hard delete
of an organization is not covered: `orgDeleted` only deactivates the org.

Value rows go with their record: reindexing replaces them and a cleared
record has none. A record deleted by any other path leaves its rows behind,
but every read joins the record, so they are never returned; the sweep
removes them in batches (`values_removed` on the sweep metric).

The sweep and the writer can reach the same node at once. Three rules keep a
live mention from being deleted:

- A writer claims its nodes before it links them, and the claim clears
  `orphanedAt`. The claim and the sweep's delete write the same node, so one of
  them waits or retries instead of the node going mid-link. Only marked nodes are
  written, so a popular entity is not rewritten on every record.
- The sweep finds its candidates, deletes their vector points, then deletes the
  nodes, checking each one is still orphaned. A node claimed in between is kept.
- After a delete, an ArangoDB edge is removed only while its target is still
  missing. A writer may have created the node again, and its edge is then live.

On ArangoDB the sweep's delete hits a write conflict when a writer changed the
node after the sweep's snapshot. Neo4j has no snapshot and does not check a
match again once it holds a lock, so its delete locks each candidate and then
checks it again. Writers and the sweep retry deadlocks and conflicts, and every
writer links and unlinks entities in node-key order, so two records never lock
the same nodes in opposite orders. On Neo4j an alias node can name several
entities; a delete removes only the swept node's alias links, and the alias
once nothing else uses it.

One indexing replica sweeps at a time, under a Redis leader lease
(`named_entity_sweep:leader`); without Redis no replica sweeps. The grace period
(`NAMED_ENTITY_ORPHAN_GRACE_SECONDS`, default 24 hours) cannot go below one hour:
a node marked after a writer claimed it is safe only while its link commits
within the grace period.

The node is the record of the clean-up still owed. If the vector delete fails,
the nodes stay and the next pass retries them. A sweep with no vector store
deletes only kinds that never get a vector point (values, patterns and PII); an
embeddable node waits for a pass that can delete its point. A failed edge
clean-up is not retried: the edge is still a true mention, reads skip it, and it
goes when its record is reindexed or deleted.

One gap remains. If a writer claims a node between the candidate lookup and the
vector delete, and writes its vector point in that window, the point is lost
until the record is reindexed. The graph is still correct; only semantic search
misses the entity. The sweep metric counts `relinked` candidates, so the rate is
visible.
