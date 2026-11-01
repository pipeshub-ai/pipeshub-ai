# Record query: exact filters, totals and links over records

Questions that filter, count, total or follow links across records ("how many
tickets did Ravi work on in March across Zendesk, Salesforce and INT", "which
customers won a deal over $100,000") are answered by one permission-scoped
query engine, not by the model reading a page of search results. Search is
top-k and lossy by design, and models miscount lists of 20 or more items; the
engine sees every record the user may read and returns exact totals.

Read this before touching `app/modules/retrieval/record_fields.py`,
`app/modules/retrieval/record_query/`, `get_record_query_candidates` in the
graph providers, or the `find_records` agent tool.

## Layers

1. **Field registry** (`record_fields.py`): every field a query may filter,
   group or total on, with its kind, the record types that carry it, where the
   graph stores it, its allowed values and what a missing value means.
2. **Query model and engine** (`record_query/`): a typed `RecordQuery`, an
   engine that pages candidates from the provider, decides each one with the
   same access check every entity tool uses (`admitted_node_ids`), and
   aggregates over admitted rows only.
3. **Provider candidates** (`IGraphDBProvider.get_record_query_candidates`):
   one window of candidate records matching the query's conditions, in a
   stable order, with the fields the engine needs. Not an access decision.
4. **Agent tool** (`knowledgegraph__find_records`): `find_records_by_entity`
   grown to take the query's conditions and aggregates; totals first.

## Field registry

| Field | Kind | Records | Stored as |
| --- | --- | --- | --- |
| `source` | value | all | `Record.connectorName` |
| `record_type` | value | all | `Record.recordType` |
| `container` | id | all | `Record.recordGroupId` (descendants included) |
| `created` | date | all | `Record.sourceCreatedAtTimestamp` (ms) |
| `updated` | date | all | `Record.sourceLastModifiedTimestamp` (ms) |
| `status` | value (`Status`) | ticket | `Ticket.status` |
| `source_status` | text | ticket | `Ticket.sourceStatus` |
| `priority` | value (`Priority`) | ticket | `Ticket.priority` |
| `resolved` | date | ticket | `Ticket.resolvedAtTimestamp` |
| `due` | date | ticket | `Ticket.dueDateTimestamp` |
| `amount` | number | deal | `Deal.amount` |
| `close_date` | date | deal | `Deal.closeDate` |
| `deal_outcome` | value: WON, LOST, OPEN | deal | derived from `Deal.isClosed`, `Deal.isWon` |
| `resolution_days` | number | ticket | derived: `resolved - created`, whole days |
| `person` | person in a role | all | `ENTITYRELATIONS` edge `edgeType` |
| `organization` | organisation | all | `ENTITYRELATIONS` `FOR_ACCOUNT` / `MENTIONS` |
| `linked_to` | record | all | `RECORD_LINK` edge, any `relationshipType` |

Each role is one stored edge type, and `record_fields.ROLE_EDGES` is the only
list of them: assignee `ASSIGNED_TO`; reporter `REPORTED_BY`; creator
`CREATED_BY`; author `AUTHORED_BY`; owner `OWNED_BY`; reviewer `REVIEWED_BY`;
editor `LAST_MODIFIED_BY`; recipient `ADDRESSED_TO`; lead `LEAD_BY`; mentioned
`MENTIONED_IN`; participant `INVOLVED_IN`. No role means any of them. A
question that means either of two roles ("who wrote it": creator or author)
says so with `any_of`. `find_records_by_person` takes the same roles, and
`involved`, its earlier name for participant. `MENTIONED_IN` and
`INVOLVED_IN` are stored person -> record (`EDGES_FROM_PERSON`), every other
role record -> person; the providers' direction tables
(`PERSON_EDGES_TO_USER`, `PERSON_EDGES_FROM_USER`) are derived from that
split. The eval harness (`kg_eval/facets.py`) files each connector slot under
the role of the edge it writes, checked by a unit test.

**A person is an email.** A human can be several graph nodes: one `users`
node per account and, for someone outside the org, a `person` node that
`record_people` writes. Nodes sharing an email (case-insensitive) are one
person; a node without one is a person on its own
(`app/modules/retrieval/person_identity.py`, provider
`get_person_nodes`). The engine expands every person condition's ids to all
nodes of those people before pushdown, inside the user's org only (an id of
another org stays as given and matches nothing); the id `me` is the user
asking. `group_by` person groups by person, keyed by one of their ids and
labelled "Name (email)". `search_entities` returns one hit per person with
`entityIds` listing the nodes it found, and `find_records_by_entity` on a
person lists every node's records. Outsiders are people the same way
members are: the entity index rebuild projects `person` nodes (org pass
version 4), indexing writes their points from `get_record_people`, and every
person path in both providers (membership, listing, the access-checked walk,
record query) reads `users` and `person` alike, each node gated to the org.
Like a member, an outsider is found only through a record the user can open.

**Unknown is never "no".** A record whose connector did not supply a field
neither matches nor fails a condition on it: the engine counts it as
`unknown` for that field and reports the count. A derived field is unknown
when any input is.

**Not classified yet is unknown too.** Topics, categories, subcategories,
departments, languages and an organisation's `MENTIONS` edges are written by
classification. With the Labs flag `ENABLE_CLASSIFY_ON_FIRST_USE`
(`docs/indexing-service.md`) a record stays `extractionStatus` NOT_STARTED
until first used, and any record is QUEUED or IN_PROGRESS while it waits; it
has none of those edges whatever it is about. So the providers push such a
condition down as "edge exists, or not classified yet" (`extractionStatus`
missing or NOT_STARTED, QUEUED, IN_PROGRESS, PAUSED), and a scan seeded from
such an entity or organisation also reads the org's records not classified
yet. A row reports which of the query's classified entities it links to
(`entities`); one that links to none and is not classified yet is unknown,
counted as `unknown["classification"]` and reported as such, never as a
miss. FAILED and SKIPPED are outcomes and stay misses. An organisation
condition still matches on a connector's `FOR_ACCOUNT` edge before
classification. When the flag is on, find_records asks for classification of
up to 50 such records a query, the shown rows first, through the same
`classify_on_first_use_later` path a search uses (each record once per ten
minutes per process); with it off nothing is queued.

## RecordQuery

```text
where:      Condition                     # optional; AND of the top-level list
  Condition = all_of[Condition] | any_of[Condition] | not(Condition)
            | field in [values]           # value fields; case-insensitive
            | field between from..to      # dates (inclusive days) and numbers
            | person(role?, ids)          # person entity ids
            | organization(ids)
            | linked_to(record ids, relation?)
            | entity(type, id)            # any entity find_records_by_entity takes
aggregate:  count | sum(field) | min(field) | max(field) | avg(field)
group_by:   field | person(role?) | organization | month(date field)  # top N groups
sort:       newest (default) | oldest | "<date or number field> asc|desc"  # one key
limit:      rows per page (default 20, max 50); cursor
```

## Result

- `total`: exact count of admitted records matching, or a lower bound with
  `approximate: true` when the scan cap or deadline was hit. Never silent.
- `value`: the aggregate, over admitted rows only.
- `groups`: `[{key, label, count, value}]`, top N by count.
- `unknown`: `{field: count}` of admitted candidates a condition could not judge;
  `classification` counts records not classified yet.
- `rows`: a page, readable names first (title, type, source, status, people
  by role, dates, amount), with record ids for fetch.
- `applied`: the conditions as the engine read them (values as stored).
- `next_cursor`, an opaque keyset cursor (see Paging), absent at the end.

## Provider contract

Two calls. The engine scans once, decides access, then reads rows by id:

`get_record_query_candidates(org_id, *, connector_ids, pushdown, scan_cap,
timeout_seconds, within)` returns `RecordCandidates(keys, capped)`:

- Candidates are records in `org_id`, in `connector_ids`, not deleted, not
  placeholders (the `_ENTITY_CANDIDATE_RECORD_FILTER` rules), narrowed by
  `within` exactly as `get_permitted_entity_records` narrows them.
- Every pushed-down condition is evaluated inside the query.
- One query per call, run once per record query: at most `scan_cap` records
  are read (`capped` says the cap was hit) and returned as keys only,
  `CandidateKey(id, sort_ts)`, in the entity candidate order (newest first by
  `sort_ts`, then id). The scan is never re-run per window, so its cost is
  linear in the cap.
- The query is built from fixed fragments with parameters; no caller text
  reaches Cypher or AQL.

`get_record_query_rows(org_id, *, record_ids, pushdown, timeout_seconds)`
returns the rows of ids the access check already admitted, read in windows
of `WINDOW` ids. Rows carry: `_key`, `recordName`, `recordType`,
`connectorName`, `connectorId`, `recordGroupId`, `webUrl`, created/updated
timestamps, the ticket fields, the deal fields, `people: [{role, id, name, email}]`,
`organizations: [{id, name}]`, `links: [{id, relation}]`, `extractionStatus`,
the fields first use needs (`indexingStatus`, `virtualRecordId`,
`contentRev`, `mimeType`), and `entities: [{type, id}]`, the query's
classified entities the record links to. A row of another org, or of a
deleted record, is never returned.

## Sorting

`newest` (the default) orders by the time the candidate order uses (source
modified time), `oldest` reverses it, and `"<field> asc"` / `"<field> desc"`
orders by one date or number field (`amount`, `created`, `resolved`, ...);
value and text fields are grouped, not sorted. Ties go newest first, then by
id. A record without the field comes after every record with it in either
direction: unknown is neither high nor low. When the scan stopped early the
order is over the records scanned, which the approximate total says.

## Paging

`next_cursor` is a keyset cursor: the place of the page's last row in the
sort order (`"<sortTs>:<id>"` for newest first), base64-encoded and tied to
the sort it was made for. It is opaque to the model, which copies it back
unchanged. The next page starts strictly after that place, so a record that
changes or arrives between calls neither repeats a row nor shifts the page.
A cursor the tool did not print for this sort is refused before any database
work. A cursor is only a place in the order: every row on every page has
passed the access check, so a forged cursor exposes nothing.

Neo4j and ArangoDB implement both calls;
`tests/integration/graph_db/test_record_query_real_backends.py` runs one
contract suite on both and compares each with a Python reference over the
same seeded graph.

## Lazy exposure

A tenant can index a whole company: millions of records, thousands of
people, organisations and containers, and each system's own status names.
None of that is sent to the model up front. What the model sees grows only
as a question needs it, in three steps:

1. **The tool loads on demand.** `find_records` is not an essential tool. It
   sits in the knowledge-graph toolset, which the model finds through
   `search_tools` and loads with `fetch_tools` (the existing lazy-toolset
   path, `tool_disclosure: lazy`). Its definition names the parameters and
   the field names from the registry, and nothing else: no values, no
   people, no containers. Adding it costs the essential tool set nothing.
2. **Fields and values are discovered, not listed.** `find_records` with
   `describe: [field, ...]` returns, within the caller's permissions and
   current conditions, the fields present for the sources and record types
   in scope and each field's top values with counts (at most 20). Unbounded
   fields (people, organisations, containers) are never enumerated; the
   model resolves a name to an id with `search_entities`, which already
   exists, and passes the id.
3. **Results teach the next step.** A response echoes the conditions as
   applied (values as stored) and, when it is truncated or large, names at
   most three narrowing fields with their top values in the matched set.

A hook may help the model find the tool, never the values: an optional
pre-model hint (context pipeline, `docs/context-engineering.md`) that, when
the toolset is granted but not loaded and the question asks to count, total
or filter, adds one line naming `find_records`. It is off by default, adds
at most about 40 tokens, and ships only after the eval shows it does not
regress other questions.

## Whole-company scale

- **Selective first.** Every query is narrowed by its most selective
  indexed condition (an entity edge, a container, a time window, a record
  type) before anything else. A query with no selective condition over a
  large tenant returns an approximate total and asks for a narrower one.
- **Count where the rule allows it.** For connectors where app access
  grants every record, totals, sums and groups are computed inside the
  database. For connectors with per-record permissions, candidates are
  paged and checked with `check_access` in batches, up to the scan cap. The
  total is exact when no cap was hit, and marked approximate when one was.
- **Indexes** back each stored field a query filters on (record org,
  connector, type and timestamps; ticket status, priority and resolved;
  deal amount and close date). They are added per backend with a migration,
  and a load test on the largest dataset we have gates them.
- **Bounded output:** at most 50 rows a page, 20 groups and 20 described
  values; every query has a timeout and a scan cap.

## Gates

1. Permission: a record the user cannot read never appears in rows, totals,
   groups, sums or links, on either backend (integration tests).
2. Contract: both backends equal the reference on every query in the suite.
3. Eval: the record-query arm is compared with the current arms on the hard
   set and the 170-question set; no question regresses beyond run noise.
4. Cost: tokens, tool calls and latency per answer reported per arm, and
   the essential tool set's definition size, which must not grow.
5. Safety: every query has a timeout and a scan cap; hitting either marks
   the result approximate.
