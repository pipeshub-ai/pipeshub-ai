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
4. **Agent tool** (`knowledgegraph__find_records`): with the record-query
   flag on, the one listing tool. It takes the conditions of the three it
   replaces (`find_records_by_entity`, `find_records_by_person`,
   `find_records_by_value`) and the query's aggregates; totals first, then
   the recall check's possible matches.

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
`INVOLVED_IN` are mostly stored person -> record (`EDGES_FROM_PERSON`), every
other role record -> person, but a connector may write one the other way
round. So every person path matches a role's edge type in either direction,
with the far end required to be a person node (`users` or `person`), which
keeps a record group's `MENTIONED_IN` out: record query, and the person
listings (`person_query_ref`, one `edgeTypes` list for both directions of
both providers' person branch), so the two never disagree on one edge. The eval harness (`kg_eval/facets.py`) files each connector slot under
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
condition down as "edge exists, or not classified yet or failed"
(`extractionStatus` missing or NOT_STARTED, QUEUED, IN_PROGRESS, PAUSED,
FAILED), and a scan seeded from such an entity or organisation also reads
the org's records in those states. A row reports which of the query's classified entities it links to
(`entities`); one that links to none and is not classified yet is unknown,
counted as `unknown["classification"]` and reported as such, never as a
miss. A record whose classification FAILED is unknown too, under its own
reason (`unknown["classification_failed"]`): its taxonomy is just as unknown,
but first use does not ask for it again, which would retry it on every query;
a reindex with `stages: ["classify"]` retries it. SKIPPED stays a miss:
classification does not apply to that record (the pipeline policy leaves its
kind unclassified, or it has nothing to classify), so it has none of those
edges by design, not by delay. An organisation
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
            | person(role?, name | ids)   # a full name, an email or "me"; or person entity ids
            | organization(ids)
            | linked_to(record ids, relation?)
            | entity(type, id)            # any entity find_records_by_entity takes
            | mentions(kind?, name?, amount?, quantity?, date?, percent?)  # a value in the text
aggregate:  count | sum(field) | min(field) | max(field) | avg(field)
group_by:   field | person(role?) | organization | month(date field)  # top N groups
sort:       newest (default) | oldest | "<date or number field> asc|desc"  # one key
limit:      rows per page (default 20, max 50); cursor
```

**A person by name** is resolved by the tool before the engine runs, as
`find_records_by_person` resolves one (`people.match_people`): `me` is the
user asking, an email or a full name goes through the provider's
`find_users`, nodes sharing an email are one person, and the first five
people found are probed with a one-record person listing in the condition's
role. One person with a readable record: the condition takes all their
nodes and the result says who they are. Several: the reply lists them to
pick by email, never a guess; the query does not run. None: the reply says
so and points at search. A person is named back only through a record the
user can read.

**Mentions** are values named-entity extraction found in a record's text
(`app/modules/named_entities`): `kind` (one or a list, any of them), `name`
(a prefix), `amount` `{from, to, currency}`, `quantity` `{from, to,
dimension}`, `date` `{from, to}`, `percent` `{from, to}` as a fraction. The
date range is inclusive by day: `YYYY-MM-DD`, or epoch ms where a UTC
midnight `to` covers that whole day (as `find_records_by_value`'s
`date_to`); the graph's exclusive end is computed from it. Only in the
top-level `where` list. The engine resolves each to the matching records of
the connectors in scope (`EntityFilterResolver`, at most the scan cap; too
many asks for a narrower one) and pushes them down as `records`, which the
scan seeds from. A mention is a text mention, never a field: it filters,
the result says so, and nothing totals or groups the mentioned values. A
record the mention did not find is unknown for it, not a miss
(`unknown["mentions"]`), which only the recall check ever meets. Mentions
need named entities on (`KnowledgeGraphPolicy.named_entities`).

## Result

- `total`: exact count of admitted records matching, or a lower bound with
  `approximate: true` when the scan cap or deadline was hit. Never silent.
- `value`: the aggregate, over admitted rows only.
- `groups`: `[{key, label, count, value}]`, top N by count.
- `unassigned`: for `group_by` organization (the one grouping classification
  feeds), the matched records in no organisation group, never merged into
  one: `unknown (not classified yet)`, `unknown (classification failed)` and
  `(no organization)` for classified records naming none. With at most one
  organisation a record, groups plus these add up to the total; a record
  naming two organisations counts in both groups.
- When records not classified yet or failed outnumber the matches (over half
  of matches plus those), one line says the answer is mostly unknown and a
  classification pass would make it exact.
- `unknown`: `{field: count}` of admitted candidates a condition could not judge;
  `classification` counts records not classified yet, `classification_failed`
  those whose classification failed.
- `rows`: a page, readable names first (title, type, source, status, people
  by role, dates, amount), with record ids for fetch. Each row gets a
  record-level Citation ID through `cite_listed_records`, as navigate,
  lookup_record and list_files rows do, quoting the row's own facts (type,
  source, status, priority, dates, amount, outcome); totals and groups are
  computed, not quoted, and are not cited.
- Under a conversation selection or bound, the result says that every figure
  counts only the sources the conversation is limited to (`LIMITED_SCOPE_NOTE`).
- `applied`: the conditions as the engine read them (values as stored).
- `next_cursor`, an opaque keyset cursor (see Paging), absent at the end.
- The recall check's line: possible matches, or why it did not run, and
  the judge's verdicts on them (below).

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

A pushdown's `records` key (ids a mention resolved to, each entry any of
its ids) is applied as an id filter and, when present, seeds the scan by id.

Neo4j and ArangoDB implement both calls;
`tests/integration/graph_db/test_record_query_real_backends.py` runs one
contract suite on both and compares each with a Python reference over the
same seeded graph.

## Recall check

Graph extraction is imperfect, and a record must never be silently lost.
Every `find_records` query runs it once, on its first page: not for a pure
`describe` (`limit` 0, no aggregate or grouping), and not on a cursor page,
which continues the same query and says the check ran on the first page.
It is a bounded hybrid search beside the engine
(`record_query/recall.py`):

- **Terms** are the query's own: people's names (resolved, or as search
  found them), entity and organisation names `search_entities` returned,
  the values of value fields (status, priority, ...; never source, record
  type or container, which are filters), and mention text (name, amounts,
  currency, percent). Never ids. A query with no such term skips the check
  and says so.
- **Search** is the search tool's retrieval call
  (`RetrievalService.search_with_filters`) within the turn's scope
  (`search_scope`: apps, knowledge bases, selection, strict scope, the
  request's entity filter) and its permission path, with the query's
  created/updated window; top 50 (`RECALL_TOP_K`), a 4-second deadline
  (`RECALL_DEADLINE_SECONDS`) for the search and again for reconciling.
- **Reconcile**: each hit the engine did not match (nor another copy of,
  by virtual record id) is run through the scan restricted to the hits
  (`records`) with the query's structural conditions only (source, type,
  container, window, link, taxonomy entity), then the access check every
  row passes, then its row is read and evaluated in recall mode: a known
  contradicting fact (another assignee in the role, another status,
  outside the window or container) drops it; what the graph cannot tell (no
  person or organisation edge, no field, not classified, no extracted
  mention) makes it a possible match.
- **Render**: the exact total as before, then "Possible matches the graph
  could not confirm: M" with up to 10 listed and cited like rows, and the
  rule that they are never added to an exact total. A failed or late search
  says the check did not run; the call itself never fails for it.
- **Logs** carry counts only (searched, outside, admitted, possible by
  connector and by condition kind) with the query's correlation id.

## Judging possible matches

When the recall check returns at least one possible match, one bounded model
call reads them (`record_query/judge.py`) and says, per candidate, whether
its text satisfies the conditions the graph could not decide. It runs only
under `KnowledgeGraphPolicy.record_query`, only on a first page, and only
with possible matches to judge; `judge.JUDGE_ENABLED` switches it off.

- **Input**: the conditions as applied (the same echo the result prints,
  with the names of the ids it holds), then up to `MAX_JUDGED` (10)
  candidates, each with the fields the graph knows (`row_view`, no id) and
  its own best passages from the recall search: at most 3, 1,200 characters
  in all (about 300 tokens). Passages are matched to a candidate by its
  record id or virtual record id only, and a candidate is a row the access
  check already admitted, so nothing the user cannot open is sent. The
  whole prompt is held under `INPUT_TOKEN_BUDGET` (5,000 estimated tokens)
  by cutting the longest passages first.
- **Model**: the request's own model (`state["llm"]`), the one the agent
  runs on. The call is made inside the tool, so it goes out under the
  `ModelCallContext` the route bound for the turn
  (`docs/llm-gateway-identity.md`), labelled `record_judge` for usage
  (`llm_usage_source`, and the `UsageLedger` when the request opted in),
  with the request's tracing callbacks. No new provider wiring.
- **Untrusted text**: each passage is fenced in `<passage>` tags (any such
  tag inside it is removed first) and the instructions say the text is
  data, never instructions. The reply must be exactly one JSON object
  `{"verdicts": [{"id": "c<n>", "verdict": "supports" | "contradicts" |
  "unclear", "quote": "..."}]}` (a code fence around it is allowed, prose is
  not). An entry naming an id that was not offered, a repeated id, another
  verdict, or a `supports` / `contradicts` whose quote is not a span of that
  candidate's own passages is `unclear`; so is a candidate the reply leaves
  out. A passage that tells the model to approve every candidate can
  therefore decide at most its own record, and only by quoting itself.
- **Render**: under the possible-matches header, a line that this is a
  model judgement, not a graph fact, and that the exact total does not
  change; then "Likely matches (model judgement, not counted): K", each
  with its `judge_quote`; then "Still possible" for the unclear; then one
  line counting the contradicted ones, which are left out of the list and
  not cited.
- **Bounds**: one call, never one per candidate; `JUDGE_DEADLINE_SECONDS`
  (5 s) covers the model lookup and the call. The output is not capped by
  a provider `max_tokens` (providers name it differently and reasoning
  models count thinking against it); ten short verdicts and the deadline
  bound it. A timeout, an error or no model leaves the possible matches
  unjudged with one line saying the judge did not run; the call never
  fails for it.
- **Logs**: one line per query with its correlation id: judged count,
  verdict counts, malformed entries, latency; or the error class when it
  did not run. No content, names or quotes.

**One call, not a sub-agent.** A domain or exploration child
(`app/agents/agent_loop/domain_agents.py`) is a ReAct loop of up to 8-12
turns with tools, started by the parent model choosing to call it, on the
runtime the tool layer does not hold. The judge needs none of that: its
inputs are already in hand (the admitted rows and the passages the recall
search returned), the decision is a fixed classification, and it must fit a
deadline inside a tool call. A loop would cost several model calls and
seconds per query, could fetch further content, and its free-text answer
would need parsing anyway. One batched call has one round trip, a fixed
input size, and a reply checked against a schema; a candidate that needs
more reading is still possible, and the agent can `fetch` it.

## One listing tool

With `KnowledgeGraphPolicy.record_query` on, the model is offered
`find_records` and none of `find_records_by_entity`,
`find_records_by_person` and `find_records_by_value`
(`entity_search_surface.MERGED_INTO_FIND_RECORDS`): not at the top level,
not by the progressive grant (which grants `find_records`), and not to the
exploration child, which takes `find_records` in place of
`find_records_by_person`. Other tools' text names `find_records` instead of
them. In the full arm with named entities the knowledge-graph tools offered
drop from 8 to 6 (timeline aside), and `find_records`' definition is no
larger than the three it replaces together (`test_find_records_surface`).

The old names stay hidden and callable for one release after this merges,
then are removed. They exist for saved agents and cached
plans: `retired_tools_for_request` serves each under its old schema, the
loader registers it with `ToolRegistry.register_hidden_tool` (callable by
exact name, in no schema list, toolset or search), and an agent saved with
one picked gets `find_records` too. Each routes to the equivalent query
(`ops/merged_listings.py`) and says the tool was merged and what to call
instead; an old integer cursor cannot continue there and says so. With
record query off, every tool is as before.

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
- **The not-yet-classified seed** reads the `(orgId, extractionStatus)`
  index on both backends (`record_org_extraction_status` in Neo4j,
  `records_org_extraction_status` in ArangoDB, hinted there), created with
  the other record indexes at startup. It reads stored statuses only: every
  record write stores one, and a record with none is still judged unknown
  wherever another seed reaches it.
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
