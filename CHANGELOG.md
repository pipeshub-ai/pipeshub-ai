# PipesHub Changelog

All notable changes to [PipesHub](https://github.com/pipeshub-ai/pipeshub-ai) — the workplace AI platform for enterprise search and agent workflows — documented in one place, newest first.

This file is the release registry: each entry is a condensed summary linking to the detailed per-release changelog in [`changelog/`](changelog/) (full PR-level accounting and author credits) and to the raw GitHub release. Versioning follows semver; **Stable** releases are recommended for production, **Beta/Alpha** channels preview the next stable.

| Version | Date | Channel | Theme |
|---|---|---|---|
| [0.10.0](#0100--2026-10-12) | Oct 12, 2026 | Stable | Trash, knowledge-graph entity tools, security sweep |
| [0.9.1](#091--2026-09-28) | Sep 28, 2026 | Stable | OAuth and PKCE hardening, Kafka fix, SDK 1.7.0 |
| [0.9.0](#090--2026-09-28) | Sep 28, 2026 | Stable | Projects, service accounts, SMB and Drupal Wiki connectors |
| [0.8.0](#080--2026-09-14) | Sep 14, 2026 | Stable | Fair connector scheduling, indexing under load, Redis Cluster |
| [0.7.0](#070--2026-08-26) | Aug 26, 2026 | Stable | PATs, GitHub connector, Bedrock, one-command installer |
| [0.6.0](#060--2026-08-10) | Aug 10, 2026 | Stable | New agent loop ships stable; query service ~1.8× faster |
| [0.6.0-beta](#060-beta--2026-08-01) | Aug 1, 2026 | Beta | New agent loop, standalone parsing & extraction, OpenSearch |
| [0.5.0](#050--2026-07-01) | Jul 1, 2026 | Stable | Atlassian Data Center, agent action tools, open model layer |
| [0.4.5](#045--2026-05-20) | May 20, 2026 | Stable | GitLab self-managed (EE), slimmer deployments |
| [0.4.4](#044--2026-05-18) | May 18, 2026 | Stable | Jira & Confluence Data Center connectors |
| [0.4.3](#043--2026-05-16) | May 16, 2026 | Stable | Production Helm chart, chat attachments, Vertex AI |
| [0.4.0](#040--2026-05-05) | May 5, 2026 | Stable | Next.js frontend; GitLab, Salesforce, Zoom connectors |
| [0.4.0-beta.2](#040-beta2--2026-05-02) | May 2, 2026 | Beta | Preview of the 0.4.0 cycle |
| [0.3.0](#030--2026-03-20) | Mar 20, 2026 | Stable | Agent toolsets, MCP server, Slack bot platform, Neo4j |
| [0.2.0](#020--2026-02-11) | Feb 11, 2026 | Stable | 15+ new connectors, multi-instance, OAuth2 provider |
| [0.1.0](#010--2025-12-23) | Dec 23, 2025 | Stable | First stable release |
| [0.1.0-beta](#010-beta--2025-12-09) | Dec 9, 2025 | Beta | Connector filters, five new sources, security sweep |
| [0.1.2-alpha](#012-alpha--2025-12-02) | Dec 2, 2025 | Alpha | Confluence & SharePoint connectors, Azure AI models |
| [0.1.1-alpha](#011-alpha--2025-11-05) | Nov 5, 2025 | Alpha | BookStack & ServiceNow connectors, SAML JIT |
| [0.1.0-alpha](#010-alpha--2025-10-28) | Oct 28, 2025 | Alpha | Founding release |

---

## Unreleased

Changes merged to `main` since the last release: [`v0.10.0...HEAD`](https://github.com/pipeshub-ai/pipeshub-ai/compare/v0.10.0...HEAD).

### Breaking changes & upgrade notes
- **A saved agent whose knowledge source lists `filters.recordGroups` or `filters.records` now searches only those nodes.** The lists were stored but not applied before, so an agent saved with non-empty lists becomes narrower on upgrade. To keep searching the whole source, tick the whole source again on that node in the agent builder, or clear the lists. A source whose stored `filters` cannot be read (text that is not JSON, or lists that are not lists of ids) is no longer searched at all, and the query service logs a warning naming it; re-save the agent to repair it. An id that is itself one of the agent's sources is not read as a limit (agents saved before each collection became its own source listed the collection there).
- **Permissions and the Knowledge Hub read a reworked graph model, and an existing graph is migrated when the connector service starts. The upgrade is one-way: take a backup of the graph database first.** Hierarchy now lives on one edge (`NODE_RELATION` on Neo4j, `nodeRelations` on ArangoDB; relationship types `PARENT_CHILD` and `ATTACHMENT`), links between records moved to their own edge (`RECORD_LINK` / `recordLinks`), and a nested record inherits from its hierarchy parent instead of from its record group. One batch check (`check_access`) now decides record open, search filtering and the agent tools, and the Knowledge Hub lists by walking that hierarchy with per-node access rules (`accessRule`: `OPEN`, `STRICT`, `RESTRICTED`). Migrations, all idempotent and each recorded under `/migrations/*` in the KV store: the edge rename runs before schema init and is the only fatal one (a failure stops the service with the data untouched, and the next start retries); the App `orgId` stamp runs next; the link split, the folder mime type rewrite, the hierarchy backfill and the listing-state stamp run in the background after the service is up, in that order, each retried with backoff until it succeeds, because on a large graph they outlast the 120 s the image's process monitor allows a service to become healthy. Until they finish an upgraded deployment lists less than it should, never more. What an old graph does not record is completed by the connector's next full sync, not by a migration: records nested under a record that carries its own grants (or under one below it) stay hidden until then, and `accessRule` values and a top-level group's inheritance from its connector are written by that sync. Run a full sync of each connector after upgrading. A release older than this one cannot read the migrated graph.
- **Access results that differ from the previous release.** A record whose parent is in another record group (a Jira story under an epic of another project) keeps its own project's audience and no longer takes the parent's. An item shared with a user on its own, whose containers the user cannot open, is listed directly under its connector for that user. A grant on one connector never admits a record of another connector. The Collection roles `ORGANIZER` and `FILEORGANIZER` are read as `READER`; re-grant `WRITER` where those users must still create folders or delete files. A SharePoint item whose permission scope cannot be read during a sync keeps its stored access (a new one is stored without inheritance) instead of being treated as inheriting. Deleting a chat attachment answers 404 for every id that is not the caller's own attachment. A search whose permission check cannot run answers "permission check unavailable" instead of an empty result.
- **Knowledge Hub listings page by cursor.** `GET /api/v1/knowledgeBase/knowledge-hub/nodes` and `.../nodes/{parentType}/{parentId}` accept `cursor` and return `pagination.nextCursor` / `prevCursor`; cursors are signed, bound to the user, the organisation, the place and the listing mode, and a refused cursor is a 400. `page` still works up to page 50 (it is served by reading the pages before it); deeper paging needs the cursor. On a cursor-paged request `pagination.page` and `totalPages` are null. Records carry `accessRule`. Removed from the connector service, with no gateway caller: `GET /api/v1/records`, `GET /api/v1/kb/records`, `POST /api/v1/kb/{kbId}/records`, `POST /api/v1/kb/{kbId}/folder/{folderId}/records` and `GET /api/v1/kb/{kbId}/folder/{folderId}/user/{userId}`.
- **Connector sync writes each batch of records in real transactions of 100.** A record batch now asks for an explicit transaction itself (the `NEO4J_EXPLICIT_TRANSACTIONS` default is unchanged for everything else), node schema validators are built once per collection instead of once per node, and repeated group and user lookups are asked once per slice. A deadlock inside a slice re-runs the batch; events, membership refreshes and blob moves are sent per committed slice. New optional settings: `KH_PLAN_KEEPER_SECONDS` (default 600, Neo4j: keeps the browse statements planned) and `KH_SCOPE_MODE`; the Labs flag `ENABLE_KH_SCOPE_LISTING` (off) serves the All records listing from scopes precomputed after each sync. The Python image gains `neo4j-rust-ext` (compiled PackStream for the Neo4j driver).
- **Classification and named-entity extraction run as their own pipeline stages, after a record is searchable, and neither waits for the other.** Summary, category, topic and department extraction and named-entity extraction ran inside the record's indexing attempt while it held its index permit, so a slow or unavailable LLM stalled unrelated records, a slow named-entity run held up the record's classification, and a retry re-downloaded, re-parsed and re-embedded the record. A record is now searchable as soon as its vectors are written. Classification then runs as a `classify` stage and named-entity extraction as an `entities` stage beside it, each with its own topic (`pipeline.classify`, `pipeline.entities`), permits (`MAX_CONCURRENT_INDEXING_LLM_CALLS` each, inside the one process-wide model cap), retries and dead-lettering. `entities` has no record status of its own: its progress is in the record's stage states. `extractionStatus` reads `QUEUED` until classification finishes, then `COMPLETED`, `SKIPPED` (with the reason) or `FAILED`; an LLM outage pauses classification without spending retries and leaves indexing throughput alone. Reindexing unchanged content with the same model, prompt and departments makes no LLM call when the reindex keeps the record's virtual record id (connector types with reconciliation enabled); other reindexes assign a new one and classify again, and `forceReindex` always does. Stage progress is kept in a new `stageStates` graph collection, and records gain a `contentRev` field; the indexing service applies this schema itself before it starts consuming, so it no longer depends on the connector service starting first. **Kafka:** the indexing service creates `pipeline.classify` and `pipeline.entities` at startup with `KAFKA_TOPIC_PARTITIONS` partitions and replication factor 1, as Node does for its topics; where its Kafka principal may not create topics, or the cluster needs more replicas, create both before upgrading. If they cannot be created, records are still indexed and searchable, classification and named entities wait, and the indexing service retries in the background (`/health` shows `stages.publisher_bound: false` until then). Lag dashboards gain the consumer groups `pipeline_classify_group` and `pipeline_entities_group`. `DEFER_EXTRACTION` is removed (it never deferred anything); drop it from custom compose files. Existing records keep their classification. The record page shows each record's parse, search and classification status, connector and collection stats count classification results, and a new "Retry failed classification" action re-runs only classification for the records where it failed (`POST /api/v1/connectors/{id}/reindex` with `stages: ["classify"]`). **Split deployments:** upgrade the Extraction service with or before the indexing service; an older Extraction service's answer to a model outage is recorded as `SKIPPED`. Records the old inline classification left with `extractionStatus` `IN_PROGRESS` (a pod stopped mid-classification) or `NOT_STARTED` (`DEFER_EXTRACTION=true`) are not picked up on their own: re-run classification for them with the same endpoint and `statusFilters: ["IN_PROGRESS", "NOT_STARTED"]` (records indexed before the upgrade get a full reindex). **Rolling back to 0.10.0 on ArangoDB** needs a data cleanup first: 0.10.0's strict records schema rejects every update to a record that carries `contentRev` or a status value it does not know. See `docs/indexing-service.md` §2.5.
- **The code-graph REST routes and MCP tools need the new `code:read` scope.** OAuth tokens and personal access tokens issued before the upgrade get no code access until they are re-issued with it; session users are unaffected. If your `.env` or Helm values set `MCP_SCOPES` explicitly, add `code:read` to it: tokens can only carry scopes listed there, and the first-run bootstrap (`bootstrap-first-run.sh`) asks for `code:read` and stops with "Scopes not allowed for this app: code:read" without it.
- **"Index Test Files" is off by default, including on existing GitHub and GitLab connectors.** Test files are synced but not indexed until it is turned on; turning it on now re-syncs the connector so files already synced are indexed. Turning it off does not remove tests that are already indexed (the code tools hide tests unless asked).

### New features
- **Named-entity extraction can be retried on its own, and the record page shows its status.** `POST /api/v1/connectors/{id}/reindex` accepts `stages: ["entities"]`: it re-runs named-entity extraction, without re-parsing or re-embedding, for the connector's records whose `entities` stage ended with a status in `statusFilters` (default `FAILED`) on their current content. `entities` has no record field, so records are selected by their stage state; it cannot be combined with `classify` in one request (400). The record's pipeline track gains an "Entities" step after "Classified", shown when the record has an `entities` stage state. Connector and collection stats gain `entitiesStatus` (record counts by that stage's status), and the stats panel shows "Retry failed entity extraction (N)" beside the classification retry when any failed.
- **Granular search: scope a chat or a search to any part of the hierarchy.** The source picker in chat is a tree: an app or a collection can be ticked as a whole, or opened to tick record groups, folders and records, and a ticked node covers everything under it. The same selection can be started from the "Chat" action on ticked rows in All Records and Collections, applies in search mode and in saved-agent and project chats, and comes back with a conversation. `filters` on `POST /api/v1/search` and on the chat stream routes accepts `recordGroups`, `records` (a folder or a record with everything under it) and `recordsExact` (that record alone) beside `apps`; `kb` is still accepted and means the same as `apps`. A request may name at most 200 ids, and a selection covering more than 50,000 nested groups and records is refused with a message (`SEARCH_SELECTION_MAX_NODES`). A selected node the user cannot read contributes nothing; a selection with nothing readable in it returns nothing, never everything. In a chat the selection holds for every knowledge tool, not only for content search: finding a record by name, opening one by id and the entity tools stay inside it too. A selection that is too large, or that touches a source not yet ready for folder-level search, answers 422 on `/search` and on the non-streaming chat routes, and ends a chat stream with an error frame (`selection_too_large` / `selection_not_ready`) before any model call.
- **A saved agent's source and a project's scope can be limited to record groups, folders and records.** In the agent builder each knowledge node has a control that limits it; project settings pick inside a connector. Chats then search only those nodes, and a selection made in the chat has to lie under them. A source's `filters` are checked when the agent is saved: `recordGroups` and `records` must be lists of ids (at most 200 per source), and a value that cannot be read, or a limit that covers more than one search may, is refused with 400 instead of being stored as "no limit".
- **Code repositories (GitHub, GitLab) get a code graph.** Code indexed from now on gets one. Repositories indexed before the upgrade are not re-indexed automatically: re-index a repository (its record group, depth -1) or the connector with status filter `COMPLETED` to build its graph. To re-index every repository whose code predates the code graph or the current code-graph version on the next start, set `CODE_GRAPH_BACKFILL=true` on the connectors service (it asks one repo every few seconds, once per code-graph version; expect a burst of indexing and embedding proportional to the repositories' size). `CODE_GRAPH_ENABLED=false` turns the code graph off: code is still indexed for search, but no blocks are projected and no edges built. Credential-like files (`.env*`, `*.pem`, `*.key`, `*.tfvars`, `id_rsa`, …) no longer get records, and repo config files (YAML, JSON, XML, Markdown) are now scanned for secrets like code is; content indexed before the upgrade is redacted when it is re-indexed. **GitLab:** repositories indexed before this release are not redacted until they are re-indexed; admins should reindex their GitLab connectors (or set `CODE_GRAPH_BACKFILL=true` for an automatic backfill). Pull-request and issue text (bodies, comments, diffs and inlined file contents, commit messages) is redacted the next time each record is indexed.
- **New code-graph settings**, all optional and passed through Docker Compose and Helm: `CODE_EDGE_BUILD_RECONCILE_INTERVAL_SECONDS` (600), `CODE_EDGE_FULL_REBUILD_INTERVAL_SECONDS` (7 days), `CODE_EDGE_BUILD_MAX_CONCURRENT` (2), `CODE_EDGE_BUILD_MAX_PER_ORG` (1), `CODE_EDGE_BUILD_MAX_BLOCKS` (300000), `CODE_EDGE_BUILD_MAX_FAILURES` (6), `CODE_FILE_CLASSIFICATION` (`true`; `false` skips the per-file LLM classification of code), `CODE_GRAPH_ENABLED` (`true`), `CODE_GRAPH_BACKFILL` (`false`), and `PARSE_MAX_ORG_SHARE` (0.5, the share of a parse pool one org may hold while another org waits). The env templates documented `MAX_DELIVERY_ATTEMPTS` as defaulting to 10; the default is 3.
- **The code-graph REST routes and MCP tools are gated by a new `code:read` scope.** See the upgrade note above.

### Improvements
- **Neo4j writes are atomic by default.** `NEO4J_EXPLICIT_TRANSACTIONS` now defaults to `true`, so a transaction block that fails midway rolls back instead of leaving the earlier statements committed. Set it to `false` to return to one auto-commit per query. Concurrent writers on shared nodes can deadlock under real transactions; the transaction helpers re-run such a block. A transaction is limited to 120 seconds on the server (`NEO4J_TRANSACTION_TIMEOUT`); a block that runs longer rolls back in full.
- **Permissions and the Knowledge Hub read one reworked graph model.** Hierarchy lives on one edge, links between records on their own, and one batch check decides record open, search filtering and the agent tools; an existing graph is migrated when the connector service starts. See the upgrade note above.
- **Knowledge Hub listings page by cursor.** The node listing routes accept `cursor` and return `pagination.nextCursor` / `prevCursor`. See the upgrade note above.
- **"Index Test Files" is off by default on GitHub and GitLab connectors.** Turning it on re-syncs the connector so files already synced are indexed. See the upgrade note above.

### Bug fixes
- **A named-entity extraction is kept with its stage, not in the stored record.** The `entities` stage wrote its extraction into the record's stored copy beside the classification, so the two stages read-modify-wrote the same document. It now keeps the extraction as its stage state's output, and classification is the stored copy's only stage writer. Persist retries, vector-only re-embeds and `named_entity_reextract` read the kept extraction, falling back to the stored section for records indexed before the stage existed; the re-extraction sweep now also replaces the kept extraction, so a later re-embed no longer writes back the shorter one. Rolling back keeps working: an older build finds no section for records indexed since and leaves their entities in the graph as they are.
- **`POST /api/v1/search` answered 500 when a hit was a code block.** A plain code block and a code group share the type name `code`, so a top-level code hit was unpacked as if it were a group (`ValueError: too many values to unpack`). It failed the whole search, most visibly app-level and group-level searches on GitHub, Jira and Slack.
- **A failed classification no longer looks like a successful one.** When the model returned nothing, the extraction fallback stored an empty-named category and deleted the record's department and topic links, and the Extraction service answered 200 with no classification, so its circuit breaker never saw an LLM outage. The fallback now keeps only the summary and leaves existing links alone; a model failure marks only `extractionStatus` FAILED (the record stays searchable); and the Extraction service answers 424 with an `error_code`: `LLM_UNAVAILABLE` when the provider cannot serve the call (classification pauses and resumes once it is back, spending no retries) or `LLM_FAILED` when the model produced nothing usable (retried, then FAILED). An organization with no LLM configured now gets `extractionStatus` SKIPPED with the reason, not FAILED; the Extraction service answers 422 `LLM_NOT_CONFIGURED`, which is neither retried nor counted against the breaker. The extraction prompt's JSON example is also fixed (doubled braces and a missing comma were reaching the model).
- **`MAX_TABLE_ROWS_FOR_LLM` now applies to every table, per record.** Only the Excel and CSV parsers honoured it; tables in PDFs, Word documents and connector blocks had every row described by the LLM (a 10,000-row PDF table was about 200 calls). The same running budget (default 1,000 rows per record) now covers all of them, and rows past it get plain `column: value` text. Expect fewer LLM calls on table-heavy documents.
- **Vector points get stable IDs.** IDs were random per write, so a retried or partially completed embedding batch left duplicate points beside the originals. Points are now keyed by virtual record, block and text, so a retry overwrites its own points and every piece of a block split to the embedding model's token limit keeps its own. Existing points are replaced the next time their record is re-indexed.
- **Smaller indexing fixes.** The SQL table/view row limit for stored records now holds when enrichment rewrites the blob (it re-stored every row); the parsing-service path validates the block container before any write, as the in-process path always did; an organization that has never saved AI settings indexes with the default embedding model (every upload failed before); the OCR decision for a PDF samples fixed pages, so a retry of the same file decides the same way; and every record status field accepts the same values, including a new `SKIPPED`. The records list shows an unrecognised status as itself rather than as "Queued".

### Security fixes
- **Concurrent classifications can no longer use another organization's model.** The in-process classifier kept the model it resolved for a document on a shared object and read it back after awaiting, so two documents from different organizations classified at the same moment could each be sent with the other's model and credentials. The model is now held per call. The indexing service also runs every message consumer's handlers on one worker event loop, and the ArangoDB, OpenSearch and Redis-vector clients keep one connection per event loop, as the accessible-records cache already did: with a second loop, ArangoDB sessions were being closed under in-flight requests ("Server disconnected"). The accessible-records cache also keeps its single-flight locks per event loop. The HTTP clients' circuit breakers are now safe to share across threads. No action required.
- **Spreadsheets processed at the same time no longer read each other's workbook.** The parsing service and the in-process indexer shared one Excel parser whose loaded workbook was instance state, and block creation awaits LLM calls between loading and reading it, so two concurrent `.xlsx` files could produce blocks from the wrong workbook, across organizations. Each document now gets its own parser. Workbook and CSV loading also moved off the indexing event loop.

### Performance & reliability
- **Indexing-time model calls share one cap, one timeout and a circuit breaker per model, and a provider outage no longer fails records.** Image description, VLM OCR and Excel/CSV table summaries called the model directly, outside the `MAX_CONCURRENT_INDEXING_LLM_CALLS` cap, and the cap itself was counted per event loop, so real concurrency was a multiple of the setting. Named-entity extraction and entity resolution now go through the same gateway too. Every indexing-time call now goes through one gateway per process (indexing, parsing and Extraction services): one cap across event loops, a 600 s ceiling on hangs, and a breaker per model (model class, model and endpoint, so one failing model does not stop the others on its endpoint) that fails fast after five consecutive provider failures and lets one real call probe after 30 s. While a provider is down, an image is left undescribed, a VLM OCR page comes back empty (an outage still stops the document so it is retried), and an Excel or CSV table gets a plain summary naming its rows and columns instead of failing the record. A classification paused by an outage waits it out however long it lasts: each hand-back waits longer (15 s, 1 min, 4 min, then every 5 min, never less than the provider's cooldown) and none counts toward the delivery backstop. The Extraction service's 424 `LLM_UNAVAILABLE` carries `Retry-After` while the provider's circuit is open. New metrics `pipeshub_indexing_llm_calls_total`, `pipeshub_indexing_llm_tokens_total` and `pipeshub_indexing_llm_breaker_open`, and an `llm_gateway` block on the `/health` of the indexing, parsing and Extraction services. The indexing service's `/health` `indexing_llm` block now reports the process-wide cap as `slots`, replacing `slots_per_loop` and `loops`; update any dashboard that reads those two keys. VLM OCR also no longer fails on an organization that has never saved AI settings.
- **Heavy documents are no longer throttled three times over in the all-in-one image.** The indexing, parsing and Docling processes each ran a resource governor on the same container's memory reading, so a PDF waited on three memory brakes in series, each at its own floor and flipping at a different moment: with memory near the soft threshold, heavy parsing slowed to a crawl with CPU idle while Docling returned 429s. The process that admits a parse under its memory brake now tells the services it calls (`x-pipeshub-admitted-in`), and a service that shares its memory admits the parse up to its own fixed ceiling instead of braking it again. Split deployments, where each service has its own memory, are unchanged. No action required.
- **Docling reports a failed or timed-out parse as HTTP 422 with an `errorCode`** (`PARSE_TIMEOUT`, `PARSE_FAILED`) instead of 200 with `success: false`. 422 rather than 5xx keeps the client from retrying a document that already used its 40-minute budget and from counting it against the circuit breaker.

- **Classification and named-entity extraction run as their own pipeline stages, after a record is searchable.** A slow or unavailable LLM no longer stalls unrelated records, and a retry no longer re-parses and re-embeds the record. See the upgrade note above.
- **Connector sync writes each batch of records in real transactions of 100.** A deadlock inside a slice re-runs the batch. See the upgrade note above.

### Deprecations & removals
- **`DEFER_EXTRACTION` is removed** (it never deferred anything); drop it from custom compose files. See the upgrade note above.
- **Five unused connector-service record routes are removed**, among them `GET /api/v1/records` and `GET /api/v1/kb/records`. See the upgrade note above.

---

## 0.10.0 — 2026-10-12

**Deleted records go to the trash, agents search the knowledge graph, and a broad security sweep** · Stable · [`v0.10.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.10.0) · [Full changelog](changelog/0.10.0.md)

A large release, 373 PRs since 0.9.1. It adds recoverable trash, knowledge-graph tools for agents, AI model discovery and prompt caching, and a security sweep that locks down the code sandbox and MCP.

### Breaking changes & upgrade notes
- `SANDBOX_MODE` must be set. `local` needs `SANDBOX_ALLOW_LOCAL=true`, and `SANDBOX_ALLOW_NETWORK` now defaults to `false`. In Helm these are `config.sandboxMode`, `config.sandboxAllowLocal` and `config.sandboxAllowNetwork` ([#3725](https://github.com/pipeshub-ai/pipeshub-ai/pull/3725), [#3803](https://github.com/pipeshub-ai/pipeshub-ai/pull/3803), [#3780](https://github.com/pipeshub-ai/pipeshub-ai/pull/3780))
- Custom STDIO MCP servers need `MCP_ALLOW_CUSTOM_STDIO=true` (Helm: `config.mcpAllowCustomStdio: true`) ([#3887](https://github.com/pipeshub-ai/pipeshub-ai/pull/3887))
- The parsing and extraction services require a service token, and the query and connector services drop wildcard credentialed CORS ([#3818](https://github.com/pipeshub-ai/pipeshub-ai/pull/3818), [#3890](https://github.com/pipeshub-ai/pipeshub-ai/pull/3890))
- `FAIR_SCHEDULING_LANE_ASSIGNMENT` (Helm `messageBroker.laneAssignment`) defaults to `assigned` on Redis. Set it to `hash` to go back ([#3982](https://github.com/pipeshub-ai/pipeshub-ai/pull/3982))
- Some status codes and fields change, for example `403` for member calls to admin routes and `blockedRecordId` on restore refusals. The full list is in the [full changelog](changelog/0.10.0.md#breaking-changes--upgrade-notes)

### New features
- Deleted records can go to the trash, be restored, and are purged after the retention period, behind the Labs setting `ENABLE_SOFT_DELETE` ([#3718](https://github.com/pipeshub-ai/pipeshub-ai/pull/3718), [#3799](https://github.com/pipeshub-ai/pipeshub-ai/pull/3799), [#3847](https://github.com/pipeshub-ai/pipeshub-ai/pull/3847))
- Agents get knowledge-graph entity tools, backed by a new permission-checked entity index ([#3308](https://github.com/pipeshub-ai/pipeshub-ai/pull/3308))
- AI models can be discovered from a provider and added several at a time, and configured models are listed at `GET /v1/models` ([#4012](https://github.com/pipeshub-ai/pipeshub-ai/pull/4012))
- Provider prompt caching for the agent loop's prompt and tool definitions ([#4038](https://github.com/pipeshub-ai/pipeshub-ai/pull/4038))
- An All Artifacts (Beta) gallery ([#3427](https://github.com/pipeshub-ai/pipeshub-ai/pull/3427))

### Improvements
- Invite emails are queued through the message broker and sent in the background ([#2906](https://github.com/pipeshub-ai/pipeshub-ai/pull/2906))

### Bug fixes
- Nine connector families (BookStack, Confluence, Zammad, Nextcloud, object stores, Drive Workspace, Web, Jira, Snowflake) now remove items deleted at the source or left out by a narrowed filter ([#3705](https://github.com/pipeshub-ai/pipeshub-ai/pull/3705), [#3709](https://github.com/pipeshub-ai/pipeshub-ai/pull/3709), [#3708](https://github.com/pipeshub-ai/pipeshub-ai/pull/3708))

### Security fixes
- The code sandbox reaches Docker only through a policy proxy, its network is off by default, and the app runs as non-root ([#3782](https://github.com/pipeshub-ai/pipeshub-ai/pull/3782), [#3780](https://github.com/pipeshub-ai/pipeshub-ai/pull/3780))
- Three GitHub security advisories are fixed: signed download URLs, `SANDBOX_MODE` fail-closed, and id encoding in service URLs ([#3723](https://github.com/pipeshub-ai/pipeshub-ai/pull/3723), [#3725](https://github.com/pipeshub-ai/pipeshub-ai/pull/3725), [#3728](https://github.com/pipeshub-ai/pipeshub-ai/pull/3728))

### Performance & reliability
- Each connector is given the least-loaded Redis Streams indexing lane and keeps it, instead of hashing onto one ([#3982](https://github.com/pipeshub-ai/pipeshub-ai/pull/3982))

---

## 0.9.1 — 2026-09-28

**OAuth and PKCE hardening, a path-traversal fix, a Kafka consumer that stops losing messages, and SDK 1.7.0** · Stable · [`v0.9.1`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.9.1) · [Full changelog](changelog/0.9.1.md)

A 20-PR security and reliability patch on top of 0.9.0, published the same day. The 1.7.0 SDKs, released on October 12, 2026, are compatible with 0.9.1. Upgrade from 0.9.0 is recommended.

### Breaking changes & upgrade notes
- OAuth access tokens and personal access tokens get `403` on the OAuth app, PAT, consent and device-flow routes ([#3626](https://github.com/pipeshub-ai/pipeshub-ai/pull/3626))
- PKCE `plain` is rejected, and public clients must send an `S256` challenge on the consent `POST` ([#3630](https://github.com/pipeshub-ai/pipeshub-ai/pull/3630))
- **SDK 1.7.0 is a minor version with breaking changes** for TypeScript, Python and Go. The fix for each change, in each language, is in the [full changelog](changelog/0.9.1.md#sdks--170-typescript-python-go)

### Bug fixes
- The Kafka consumer used by the connectors and query services no longer loses messages after a temporary failure ([#3515](https://github.com/pipeshub-ai/pipeshub-ai/pull/3515))

### Security fixes
- Graph enrichment permission-checks the related records it adds to a chat prompt ([#3590](https://github.com/pipeshub-ai/pipeshub-ai/pull/3590))
- OAuth app, personal access token, consent and device-flow routes require a user's sign-in session ([#3626](https://github.com/pipeshub-ai/pipeshub-ai/pull/3626))
- PKCE is enforced where the authorization code is issued and redeemed; only `S256` is accepted ([#3630](https://github.com/pipeshub-ai/pipeshub-ai/pull/3630))
- The record convert API no longer writes files outside its temp folder ([#3628](https://github.com/pipeshub-ai/pipeshub-ai/pull/3628))

---

## 0.9.0 — 2026-09-28

**Projects, service accounts and device-code login arrive, with SMB and Drupal Wiki connectors** · Stable · [`v0.9.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.9.0) · [Full changelog](changelog/0.9.0.md)

About 290 PRs since 0.8.0. Projects, identities for automation and three new connectors arrive; the rest makes failures visible instead of silent.

### Breaking changes & upgrade notes
- Each GitHub and GitLab connector instance syncs exactly one repository; split an instance that syncs several ([#3251](https://github.com/pipeshub-ai/pipeshub-ai/pull/3251))
- Each organization is limited to 5 admins, counting pending invites ([#3284](https://github.com/pipeshub-ai/pipeshub-ai/pull/3284))
- Sign-in no longer reveals whether an email has an account; clients that matched on the old answers need to change ([#3529](https://github.com/pipeshub-ai/pipeshub-ai/pull/3529))

### New features
- **Projects**: a shared workspace for chats with members, its own knowledge base, pinning and archiving ([#3304](https://github.com/pipeshub-ai/pipeshub-ai/pull/3304), [#3426](https://github.com/pipeshub-ai/pipeshub-ai/pull/3426))
- **Service accounts and service tokens**, so automation reads with its own permissions, and a **device-code login** for coding agents ([#3420](https://github.com/pipeshub-ai/pipeshub-ai/pull/3420), [#3422](https://github.com/pipeshub-ai/pipeshub-ai/pull/3422), [#3197](https://github.com/pipeshub-ai/pipeshub-ai/pull/3197))
- **SMB 2/3, SMB1/CIFS and Drupal Wiki connectors**; PAT setup for GitHub Teams and GitLab; a "Folders" filter for the object stores ([#3533](https://github.com/pipeshub-ai/pipeshub-ai/pull/3533), [#3466](https://github.com/pipeshub-ai/pipeshub-ai/pull/3466), [#3325](https://github.com/pipeshub-ai/pipeshub-ai/pull/3325), [#3340](https://github.com/pipeshub-ai/pipeshub-ai/pull/3340))

### Improvements
- BM25 keyword scoring applies IDF ([#3488](https://github.com/pipeshub-ai/pipeshub-ai/pull/3488))

### Bug fixes
- A failed read is no longer treated as "nothing there", so a database restart cannot look like a deletion ([#3444](https://github.com/pipeshub-ai/pipeshub-ai/pull/3444), [#3474](https://github.com/pipeshub-ai/pipeshub-ai/pull/3474), [#3521](https://github.com/pipeshub-ai/pipeshub-ai/pull/3521))

### Performance & reliability
- Search filters on the user's accessible containers inside the vector query instead of listing every visible record ([#3230](https://github.com/pipeshub-ai/pipeshub-ai/pull/3230))
- Domain events go through an outbox, so a broker outage delays them instead of losing them ([#3445](https://github.com/pipeshub-ai/pipeshub-ai/pull/3445))

---

## 0.8.0 — 2026-09-14

**Fair scheduling across connectors, an indexing pipeline that holds up under load, and Redis Cluster readiness** · Stable · [`v0.8.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.8.0) · [Full changelog](changelog/0.8.0.md)

103 PRs since 0.7.0, centred on the indexing pipeline and on Redis. Several Redis, TLS and vector-store settings now fail at startup instead of being ignored.

### Breaking changes & upgrade notes
- Redis TLS is enforced in every client, TLS values parse strictly, `REDIS_DB` is deprecated, and the crawling queue moves to the `{crawling}` prefix ([#3155](https://github.com/pipeshub-ai/pipeshub-ai/pull/3155))
- MD5 deduplication is per organization, so content shared across orgs re-indexes once per org ([#3112](https://github.com/pipeshub-ai/pipeshub-ai/pull/3112))
- Python services accept scoped-secret tokens only as service tokens with service scopes ([#3283](https://github.com/pipeshub-ai/pipeshub-ai/pull/3283))

### New features
- Fair scheduling: each connector is hashed onto one of a pool of lanes, so one connector's backlog no longer blocks the others ([#3126](https://github.com/pipeshub-ai/pipeshub-ai/pull/3126))
- One Redis connection provider per backend, with Redis Cluster and AWS MemoryDB support ([#3155](https://github.com/pipeshub-ai/pipeshub-ai/pull/3155), [#3254](https://github.com/pipeshub-ai/pipeshub-ai/pull/3254))
- `VECTOR_COLLECTION_STRATEGY`, and S3 authentication with an instance IAM role ([#3112](https://github.com/pipeshub-ai/pipeshub-ai/pull/3112), [#3143](https://github.com/pipeshub-ai/pipeshub-ai/pull/3143))

### Improvements
- Chat messages move into their own collections through a one-time migration ([#3148](https://github.com/pipeshub-ai/pipeshub-ai/pull/3148))

### Bug fixes
- Records no longer stay in `QUEUED` forever when their event is lost or abandoned ([#3163](https://github.com/pipeshub-ai/pipeshub-ai/pull/3163), [#3140](https://github.com/pipeshub-ai/pipeshub-ai/pull/3140))

### Performance & reliability
- Indexing concurrency follows what downstream services can take, and backs off when they struggle ([#3170](https://github.com/pipeshub-ai/pipeshub-ai/pull/3170), [#3174](https://github.com/pipeshub-ai/pipeshub-ai/pull/3174))

---

## 0.7.0 — 2026-08-26

**Personal access tokens, a GitHub connector, AWS Bedrock, and a one-command installer** · Stable · [`v0.7.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.7.0) · [Full changelog](changelog/0.7.0.md)

~100 PRs since 0.6.0. PipesHub opens up to external tooling: scoped personal access tokens for the API and MCP server, an MCP server on the new agent loop, and a one-command Docker installer for self-hosting, alongside a GitHub teams connector, a code parser, multimodal embeddings, and AWS Bedrock.

### Breaking changes & upgrade notes
- Admin privilege migrates from admin user groups to `User.role` on first boot; users without a role in their JWT must re-login ([#2939](https://github.com/pipeshub-ai/pipeshub-ai/pull/2939)). A storage-record `orgId` backfill also runs at startup ([#3059](https://github.com/pipeshub-ai/pipeshub-ai/pull/3059)).
- Compose containers are recreated and renamed to `{project}-{service}-1`; `docker exec pipeshub-ai` becomes `docker compose -p pipeshub-ai exec -T pipeshub-ai …` ([#2634](https://github.com/pipeshub-ai/pipeshub-ai/pull/2634)).
- MCP is toggled from Labs only (`PIPESHUB_ENABLE_MCP` removed), code execution is env-only, and Actions sit behind a new `ENABLE_ACTIONS` flag ([#3044](https://github.com/pipeshub-ai/pipeshub-ai/pull/3044), [#3045](https://github.com/pipeshub-ai/pipeshub-ai/pull/3045)). Legacy Docling PDF/block endpoints were removed ([#2950](https://github.com/pipeshub-ai/pipeshub-ai/pull/2950)).

### New features
- Personal access tokens: scoped, revocable, `phpat_`-prefixed credentials any member can mint for MCP and API access ([#2933](https://github.com/pipeshub-ai/pipeshub-ai/pull/2933), [#2971](https://github.com/pipeshub-ai/pipeshub-ai/pull/2971)).
- GitHub teams connector ([#2989](https://github.com/pipeshub-ai/pipeshub-ai/pull/2989)), HackerNews datasource ([#3060](https://github.com/pipeshub-ai/pipeshub-ai/pull/3060)), and a code parser with correct citations into source files ([#3014](https://github.com/pipeshub-ai/pipeshub-ai/pull/3014)).
- Multimodal embeddings and an EPUB parser ([#2919](https://github.com/pipeshub-ai/pipeshub-ai/pull/2919)), AWS Bedrock as a model provider ([#3062](https://github.com/pipeshub-ai/pipeshub-ai/pull/3062)), and pasted text as a chat attachment ([#2889](https://github.com/pipeshub-ai/pipeshub-ai/pull/2889)).
- One-command Docker installer (`curl | bash` or `./install.sh`) with image refresh, host checks, and clearer first-run failures ([#2634](https://github.com/pipeshub-ai/pipeshub-ai/pull/2634)).

### Improvements
- MCP server runs on the new agent loop ([#2830](https://github.com/pipeshub-ai/pipeshub-ai/pull/2830)); Docling processing split into stateless services with adaptive parsing ([#2950](https://github.com/pipeshub-ai/pipeshub-ai/pull/2950), [#2980](https://github.com/pipeshub-ai/pipeshub-ai/pull/2980)).

### Bug fixes
- Vector cleanup no longer lost on record delete when the broker publish fails ([#3013](https://github.com/pipeshub-ai/pipeshub-ai/pull/3013)); Drive/Gmail record streaming covers all permissioned users ([#2965](https://github.com/pipeshub-ai/pipeshub-ai/pull/2965)); Gmail shared-mailbox links point at the right account ([#3011](https://github.com/pipeshub-ai/pipeshub-ai/pull/3011)).
- Notion reindexing and API resilience, MCP tool-argument schema loss, streamed-table flicker, and a long tail of parsing and UI fixes.

### Security fixes
- `x-is-admin` header injection closed; admin status derives from the JWT role ([#2985](https://github.com/pipeshub-ai/pipeshub-ai/pull/2985)).
- Prompt-injection guard on sub-agent execution ([#2987](https://github.com/pipeshub-ai/pipeshub-ai/pull/2987)).

### Performance & reliability
- Query service throughput phase two: cached accessible-record IDs, batched graph/storage lookups, optional direct provider transports; p50 turn latency 34.4 s → 27.9 s, Node gateway p50 2,738 ms → 573 ms ([#2963](https://github.com/pipeshub-ai/pipeshub-ai/pull/2963)).

---

## 0.6.0 — 2026-08-10

**The new agent loop ships stable, and the query service gets ~1.8× faster** · Stable · [`v0.6.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.6.0) · [Full changelog](changelog/0.6.0.md)

Finalizes [0.6.0-beta](#060-beta--2026-08-01) with ~50 additional hardening PRs (~195 total since 0.5.0). The rewritten agent runtime becomes the sole execution mode, and a query-service performance overhaul roughly doubles chat throughput.

### Breaking changes & upgrade notes
- Knowledge-base record groups migrate to connectors on upgrade ([#2650](https://github.com/pipeshub-ai/pipeshub-ai/pull/2650)).
- Parsing and extraction run as separate flag-gated services ([#2541](https://github.com/pipeshub-ai/pipeshub-ai/pull/2541), [#2734](https://github.com/pipeshub-ai/pipeshub-ai/pull/2734)).
- `GRAPH_DB_TYPE` removed; Helm chart defaults the message broker to Redis with Kafka/ZooKeeper conditional ([#2887](https://github.com/pipeshub-ai/pipeshub-ai/pull/2887)).
- Atlassian site URL removed from connector configuration ([#2693](https://github.com/pipeshub-ai/pipeshub-ai/pull/2693)); Docker image RAM limit raised ([#2853](https://github.com/pipeshub-ai/pipeshub-ai/pull/2853)).

### New features
- The new agent loop is now the only agent execution mode ([#2702](https://github.com/pipeshub-ai/pipeshub-ai/pull/2702), [#2879](https://github.com/pipeshub-ai/pipeshub-ai/pull/2879)), with tuned prompts, the Slack bot migrated onto it, and its navigate/lookup tools exposed over HTTP for MCP ([#2937](https://github.com/pipeshub-ai/pipeshub-ai/pull/2937)). Reasoning-effort support runs through agents, indexing, and the OpenAPI spec.
- Omnigent integration for the PipesHub MCP server under `integrations/omnigent/` ([#2902](https://github.com/pipeshub-ai/pipeshub-ai/pull/2902)); MCP server updated to v2.2.0.
- DOCX and CSV chat attachments ([#2894](https://github.com/pipeshub-ai/pipeshub-ai/pull/2894)); bulk user invites from CSV/Excel now include a pre-send review step ([#2912](https://github.com/pipeshub-ai/pipeshub-ai/pull/2912)).

### Bug fixes
- Redis Streams producer no longer gets permanently poisoned after a `disconnect()` — the client is recreated on reconnect ([#2867](https://github.com/pipeshub-ai/pipeshub-ai/pull/2867)).
- OpenAI Responses API compatibility: correct token-limit truncation detection and normalization of structured responses ([#2876](https://github.com/pipeshub-ai/pipeshub-ai/pull/2876), [#2917](https://github.com/pipeshub-ai/pipeshub-ai/pull/2917)).
- GitLab connector fixes, AQL `coalesce` → `not_null`, chat markdown with `$` signs, citation popover, artifact creation, and a long tail of UI and indexing fixes.

### Performance & reliability
- Query-service performance overhaul: ~1.8× throughput, p50 turn latency halved (36 s → 17.8 s), SSE traffic down 28× ([#2872](https://github.com/pipeshub-ai/pipeshub-ai/pull/2872)).

---

## 0.6.0-beta — 2026-08-01

**New agent loop, standalone parsing & extraction services, OpenSearch arrives** · Beta · [`v0.6.0-beta`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.6.0-beta) · [Full changelog](changelog/0.6.0-beta.md) · *Finalized as [0.6.0](#060--2026-08-10)*

Roughly 140 PRs reworking two core engines: the agent runtime gets its new loop implementation, and document parsing/data extraction split out of indexing into their own services.

### New features
- New agent loop implementation ([#2702](https://github.com/pipeshub-ai/pipeshub-ai/pull/2702)).
- Standalone parsing and extraction services behind a deployment flag ([#2541](https://github.com/pipeshub-ai/pipeshub-ai/pull/2541)), with health checks, concurrency hardening, and base64-free PDF transport to Docling.
- OpenSearch as a supported search backend ([#2547](https://github.com/pipeshub-ai/pipeshub-ai/pull/2547)); web connectors rebuilt on crawl4ai ([#2641](https://github.com/pipeshub-ai/pipeshub-ai/pull/2641)).
- Bulk user invites via CSV/Excel upload ([#2715](https://github.com/pipeshub-ai/pipeshub-ai/pull/2715)); Slack personal connector ([#2801](https://github.com/pipeshub-ai/pipeshub-ai/pull/2801)); Box shared-files sync; Google Drive shared-drive and folder filters.
- Jira DC and Confluence DC agent toolsets ([#2661](https://github.com/pipeshub-ai/pipeshub-ai/pull/2661), [#2667](https://github.com/pipeshub-ai/pipeshub-ai/pull/2667)).

### Improvements
- Docling is the default PDF parser with OCR fallback ([#2724](https://github.com/pipeshub-ai/pipeshub-ai/pull/2724), [#2672](https://github.com/pipeshub-ai/pipeshub-ai/pull/2672)); OCR page caps, per-call VLM-OCR instances, memory/latency optimizations, and batched table-indexing LLM calls.
- Retrieval context enriched with related records from graph edges (parent/child/attachment metadata) ([#2596](https://github.com/pipeshub-ai/pipeshub-ai/pull/2596)).

### Bug fixes
- Indexing failures propagate to MD5 duplicates, and PPT/PPTX previews render reliably with CJK fonts.

### Performance & reliability
- MongoDB deployment stability, message-broker retry support, EMFILE file-descriptor limits, and Outlook long-lived-connection transport.

---

## 0.5.0 — 2026-07-01

**Jira & Confluence go Data Center, agents get real tools, model providers open up** · Stable · [`v0.5.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.5.0) · [Full changelog](changelog/0.5.0.md)

A large stable release: 230+ PRs since 0.4.5. Enterprise self-hosted reach, agents that act rather than just answer, and an open model layer.

### New features
- Full Jira and Confluence **Data Center** support — roles, groups, permissions, comments, attachments, audit logs, retries ([#2435](https://github.com/pipeshub-ai/pipeshub-ai/pull/2435), [#2528](https://github.com/pipeshub-ai/pipeshub-ai/pull/2528)).
- Agent action tools: Salesforce (e.g. pricebook updates), Google Drive files, upload/fetch storage, and an ask-user-question tool for mid-run clarification ([#2223](https://github.com/pipeshub-ai/pipeshub-ai/pull/2223), [#2210](https://github.com/pipeshub-ai/pipeshub-ai/pull/2210), [#2299](https://github.com/pipeshub-ai/pipeshub-ai/pull/2299), [#2238](https://github.com/pipeshub-ai/pipeshub-ai/pull/2238)).
- First-class OpenRouter, LiteLLM proxy, and LM Studio providers ([#2621](https://github.com/pipeshub-ai/pipeshub-ai/pull/2621), [#2628](https://github.com/pipeshub-ai/pipeshub-ai/pull/2628)); per-function model roles; a dedicated local embedding service ([#2466](https://github.com/pipeshub-ai/pipeshub-ai/pull/2466)).
- New parsers: MarkItDown, pdfplumber, selectolax; code-file indexing from knowledge bases; an in-app notification service over Kafka → Node.js → WebSockets ([#2431](https://github.com/pipeshub-ai/pipeshub-ai/pull/2431)).
- One-command installation script for self-hosting ([#2590](https://github.com/pipeshub-ai/pipeshub-ai/pull/2590)); Linear API-token auth.

### Improvements
- Confluence content pipeline rebuilt on ADF → blocks (comments included) instead of raw HTML pass-through ([#2497](https://github.com/pipeshub-ai/pipeshub-ai/pull/2497)).
- GitLab connector matured: incremental sync, repo-tree pagination, batch code-file fetching, and a major sync speedup ([#2438](https://github.com/pipeshub-ai/pipeshub-ai/pull/2438), [#2443](https://github.com/pipeshub-ai/pipeshub-ai/pull/2443)).

### Bug fixes
- OAuth errors now surface to the frontend.

### Performance & reliability
- Memory leaks across services ([#2408](https://github.com/pipeshub-ai/pipeshub-ai/pull/2408)); new ArangoDB indexes; Qdrant delete-points API for embedding deletion; Redis deadlock on unparseable messages; etcd watcher leak.

---

## 0.4.5 — 2026-05-20

**GitLab self-managed (EE) support and slimmer deployments** · Stable · [`v0.4.5`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.5) · [Full changelog](changelog/0.4.5.md)

A ~20-PR patch release.

### New features
- GitLab connector support for self-managed Enterprise Edition instances ([#2337](https://github.com/pipeshub-ai/pipeshub-ai/pull/2337)).
- Multiple authentication mechanisms per org side by side ([#2341](https://github.com/pipeshub-ai/pipeshub-ai/pull/2341)).

### Improvements
- Slimmer Docker image ([#2325](https://github.com/pipeshub-ai/pipeshub-ai/pull/2325)).

### Bug fixes
- SAML JIT provisioning, Arango cleanup on connector-instance deletion, and deep-agent conversation bleed.

### Performance & reliability
- Redis consumers drop unparsable messages instead of retrying them forever ([#2335](https://github.com/pipeshub-ai/pipeshub-ai/pull/2335)).

### Deprecations & removals
- Port 3001 exposed directly and the separate connector public URL removed ([#2326](https://github.com/pipeshub-ai/pipeshub-ai/pull/2326)).

---

## 0.4.4 — 2026-05-18

**Jira and Confluence Data Center connectors** · Stable · [`v0.4.4`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.4) · [Full changelog](changelog/0.4.4.md)

A quick ~20-PR patch bringing on-prem Atlassian into PipesHub search.

### New features
- Confluence Data Center connector ([#2302](https://github.com/pipeshub-ai/pipeshub-ai/pull/2302)) and Jira Data Center connector ([#2303](https://github.com/pipeshub-ai/pipeshub-ai/pull/2303)).

### Improvements
- Docker image optimized for size and build time ([#2304](https://github.com/pipeshub-ai/pipeshub-ai/pull/2304)).

### Bug fixes
- PPTX format detection, Mermaid rendering, chat image overflow, forgot-password error display, and connector-filter pruning.

---

## 0.4.3 — 2026-05-16

**Production-ready Kubernetes deploys, chat attachments, and Vertex AI** · Stable · [`v0.4.3`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.3) · [Full changelog](changelog/0.4.3.md)

First patch series on 0.4.x, ~90 PRs.

### New features
- Production-grade Helm chart: StatefulSets, horizontal pod autoscaling, Kind-based dev flow ([#2179](https://github.com/pipeshub-ai/pipeshub-ai/pull/2179)).
- Chat attachments (PDF/JPG/PNG) from the UI and Slack ([#2190](https://github.com/pipeshub-ai/pipeshub-ai/pull/2190)); Google Vertex AI provider ([#2279](https://github.com/pipeshub-ai/pipeshub-ai/pull/2279)); Exa web-search engine ([#2241](https://github.com/pipeshub-ai/pipeshub-ai/pull/2241)).
- PipesHub CLI for environment setup and configuration ([#1772](https://github.com/pipeshub-ai/pipeshub-ai/pull/1772)); SharePoint toolset.

### Improvements
- Search and conversation APIs made SDK-ready with an OpenAPI spec cleaned up to match reality ([#2267](https://github.com/pipeshub-ai/pipeshub-ai/pull/2267)).

### Bug fixes
- Deletion guards for models/connectors that agents depend on; proactive token refresh; knowledge-hub memory-exceed fix; broad connector, citation, and UI fixes.

### Security fixes
- Secrets masked in configuration API responses ([#2293](https://github.com/pipeshub-ai/pipeshub-ai/pull/2293)).

---

## 0.4.0 — 2026-05-05

**Next.js frontend replaces the React UI; GitLab, Salesforce, and Zoom connectors land** · Stable · [`v0.4.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.0) · [Full changelog](changelog/0.4.0.md)

A very large release — 380+ PRs since 0.3.0.

### Breaking changes & upgrade notes
- Legacy React frontend removed — Next.js is the only UI ([#2169](https://github.com/pipeshub-ai/pipeshub-ai/pull/2169)).
- Python services moved to Python 3.12 ([#1755](https://github.com/pipeshub-ai/pipeshub-ai/pull/1755)); single backend URL; SMTP step removed from onboarding; backend switched to the new custom MCP server ([#2185](https://github.com/pipeshub-ai/pipeshub-ai/pull/2185)); dev compose defaults to Neo4j.

### New features
- Coding sandbox with artifacts and an image-generation model type ([#2031](https://github.com/pipeshub-ai/pipeshub-ai/pull/2031)); speech-to-text and text-to-speech model types; web search for the assistant ([#1903](https://github.com/pipeshub-ai/pipeshub-ai/pull/1903)); deep-agent critic + reflector loop ([#1739](https://github.com/pipeshub-ai/pipeshub-ai/pull/1739)).
- Connectors: GitLab v1 ([#1857](https://github.com/pipeshub-ai/pipeshub-ai/pull/1857)), Salesforce connector + toolset ([#1787](https://github.com/pipeshub-ai/pipeshub-ai/pull/1787), [#1862](https://github.com/pipeshub-ai/pipeshub-ai/pull/1862)), Zoom connector + toolset, personal Outlook ([#1756](https://github.com/pipeshub-ai/pipeshub-ai/pull/1756)).
- First-class toolset pages plus Redshift, OneDrive, and Lumos toolsets; developer-settings page for OAuth 2.0 apps ([#1913](https://github.com/pipeshub-ai/pipeshub-ai/pull/1913)).
- Indexing reconciliation and SQL connectors ([#1568](https://github.com/pipeshub-ai/pipeshub-ai/pull/1568)); MiniMax provider; agent service accounts.
- Deployment: Redis as an optional Kafka replacement ([#1823](https://github.com/pipeshub-ai/pipeshub-ai/pull/1823)), Windows support, hot-reloading dev composes, log rotation, S3-compatible internal blob storage.

### Improvements
- The React SPA was rewritten as a Next.js application ([#1882](https://github.com/pipeshub-ai/pipeshub-ai/pull/1882)).
- Word-by-word answer streaming replaces large chunks ([#2141](https://github.com/pipeshub-ai/pipeshub-ai/pull/2141)); config reads no longer served from cache.

### Bug fixes
- Hundreds of UI, citation, and connector fixes.

### Security fixes
- Reset-password hardened against account enumeration ([#2041](https://github.com/pipeshub-ai/pipeshub-ai/pull/2041)); blocked-user cooling period.

### Performance & reliability
- Indexing throughput improved in two rounds; ArangoDB deadlock retries; frontend OOM prevention.

---

## 0.4.0-beta.2 — 2026-05-02

**Next.js frontend previews; GitLab, Salesforce, and Zoom connectors arrive** · Beta · [`v0.4.0-beta.2`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.0-beta.2) · [Full changelog](changelog/0.4.0-beta.2.md) · *Finalized as [0.4.0](#040--2026-05-05)*

The pre-release cut of the 0.4.0 cycle (350+ PRs). Previews the Next.js frontend alongside the legacy UI (removed in stable) and carries the same connector, sandbox, web-search, and deployment additions finalized in 0.4.0 above.

---

## 0.3.0 — 2026-03-20

**Agents get toolsets and MCP, Slack becomes a real bot platform, Neo4j joins as a graph option** · Stable · [`v0.3.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.3.0) · [Full changelog](changelog/0.3.0.md)

150+ PRs; squarely an AI-platform release.

### Breaking changes & upgrade notes
- Deprecated endpoints and the signed-URL download route removed ([#1470](https://github.com/pipeshub-ai/pipeshub-ai/pull/1470)); `connector_disabled`/`paused` indexing statuses removed; manual-sync connectors no longer sync on startup.

### New features
- Agent toolsets — Jira, ClickUp, MariaDB, Microsoft Teams, Outlook, GitHub — and a deep-agent mode ([#1506](https://github.com/pipeshub-ai/pipeshub-ai/pull/1506), [#1642](https://github.com/pipeshub-ai/pipeshub-ai/pull/1642)).
- MCP server bundled with the main service ([#1611](https://github.com/pipeshub-ai/pipeshub-ai/pull/1611)) on a matured OAuth2 stack (RFC 9728 resource metadata, developer settings, Cursor support).
- Neo4j as an alternative graph database alongside ArangoDB, including optional Helm support ([#1427](https://github.com/pipeshub-ai/pipeshub-ai/pull/1427)).
- Seven new datasources: RSS, ClickHouse, Lattice, Databricks, Lumos, ClickUp, personal GitHub.
- JIT SAML SSO ([#1502](https://github.com/pipeshub-ai/pipeshub-ai/pull/1502)); AWS Bedrock IAM-role credentials from EC2; OpenCV + PyMuPDF parser.

### Improvements
- Slack bot: streamed answers via the chat stream API, multiple bots per deployment, multi-user threads with @mention resolution ([#1526](https://github.com/pipeshub-ai/pipeshub-ai/pull/1526), [#1538](https://github.com/pipeshub-ai/pipeshub-ai/pull/1538), [#1593](https://github.com/pipeshub-ai/pipeshub-ai/pull/1593)).
- Web connector reliability overhaul: generator-based crawling, retries, domain scoping, better HTML cleaning.

### Bug fixes
- Async connector-instance deletion; SharePoint Graph API pagination.

### Security fixes
- Cross-connector record leakage from identical `virtualId`s ([#1699](https://github.com/pipeshub-ai/pipeshub-ai/pull/1699)).

### Performance & reliability
- Knowledge Hub search/filter latency.

---

## 0.2.0 — 2026-02-11

**The connector fleet goes wide: 15+ new sources, multi-instance connectors, and a built-in OAuth2 provider** · Stable · [`v0.2.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.2.0) · [Full changelog](changelog/0.2.0.md)

260+ PRs turning the connector layer into a real platform.

### Breaking changes & upgrade notes
- KB → connector migration runs on upgrade; "Public connector URL" setting removed; individual account creation removed; new env vars for rate limiting and token lifetimes.

### New features
- 15+ connectors/datasources: Jira, Box, S3, MinIO, GCS, Azure Blob, Azure Files, Nextcloud, Notion, Linear, Zammad, Zoom, dedicated Google Drive personal/enterprise, plus Monday, Snowflake, and Salesforce clients.
- Multi-instance connectors with per-instance credentials and sync filters ([#1151](https://github.com/pipeshub-ai/pipeshub-ai/pull/1151)).
- PipesHub as an OAuth2.0 provider with JIT account provisioning ([#1321](https://github.com/pipeshub-ai/pipeshub-ai/pull/1321), [#1347](https://github.com/pipeshub-ai/pipeshub-ai/pull/1347)).
- Knowledge Hub API unifying browse and search ([#1232](https://github.com/pipeshub-ai/pipeshub-ai/pull/1232)); VLM-based OCR; LLM header detection for spreadsheets; Helm chart; Kafka SASL; Redis TLS.

### Improvements
- Knowledge bases re-architected as connectors (renamed "Collections") with automated migration ([#1371](https://github.com/pipeshub-ai/pipeshub-ai/pull/1371)).
- New graph-database abstraction layer: `IGraphDBProvider`, fully async ArangoDB, Node.js API's direct Arango dependency removed ([#1145](https://github.com/pipeshub-ai/pipeshub-ai/pull/1145), [#1455](https://github.com/pipeshub-ai/pipeshub-ai/pull/1455)).
- Citations overhauled: filenames, grouping, and correct numbering across streaming and overflow cases.

### Security fixes
- Token invalidation on logout, captcha on auth flows, user-enumeration fix, and the MongoDB "Mongobleed" CVE-2025-14847 fix ([#1252](https://github.com/pipeshub-ai/pipeshub-ai/pull/1252)).

---

## 0.1.0 — 2025-12-23

**First stable release: security hardening and connector filters mature** · Stable · [`0.1.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/0.1.0) · [Full changelog](changelog/0.1.0.md)

Finalizes the beta and alpha series; 23 PRs on top of the beta, focused on production readiness.

### New features
- Connector filters extended to Outlook and Confluence with manually triggered filtered syncs ([#1122](https://github.com/pipeshub-ai/pipeshub-ai/pull/1122), [#1132](https://github.com/pipeshub-ai/pipeshub-ai/pull/1132)).
- SharePoint page indexing ([#1129](https://github.com/pipeshub-ai/pipeshub-ai/pull/1129)); Google Slides/Docs/Sheets on the unified blocks pipeline ([#1130](https://github.com/pipeshub-ai/pipeshub-ai/pull/1130)); Trello datasource.

### Security fixes
- Configurable JWT expiry, stack traces stripped from API responses, stricter validation middleware ([#1153](https://github.com/pipeshub-ai/pipeshub-ai/pull/1153), [#1155](https://github.com/pipeshub-ai/pipeshub-ai/pull/1155), [#1157](https://github.com/pipeshub-ai/pipeshub-ai/pull/1157)).

---

## 0.1.0-beta — 2025-12-09

**Connector filters, five new data sources, and a security hardening sweep** · Beta · [`0.1.0-beta`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/0.1.0-beta) · [Full changelog](changelog/0.1.0-beta.md) · *Finalized as [0.1.0](#010--2025-12-23)*

40 PRs pushing toward stable.

### New features
- Connector filters scoping what gets synced ([#1058](https://github.com/pipeshub-ai/pipeshub-ai/pull/1058)); Dropbox, NextCloud, Trello, Bitbucket, and Workday data sources; MD5-checksum file version control ([#1091](https://github.com/pipeshub-ai/pipeshub-ai/pull/1091)); team management UI; Opik LLM observability.

### Bug fixes
- HTTP connections no longer die after ~a day of uptime ([#1099](https://github.com/pipeshub-ai/pipeshub-ai/pull/1099)).

### Security fixes
- A sustained dependency-security sweep.

---

## 0.1.2-alpha — 2025-12-02

**Confluence and SharePoint connectors land, Azure AI models, indexing pipeline overhaul** · Alpha · [`0.1.2-alpha`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/0.1.2-alpha) · [Full changelog](changelog/0.1.2-alpha.md)

The largest point release of the alpha line (72 PRs).

### New features
- Confluence ([#1017](https://github.com/pipeshub-ai/pipeshub-ai/pull/1017)) and SharePoint ([#1018](https://github.com/pipeshub-ai/pipeshub-ai/pull/1018)) connectors; Azure-hosted OpenAI/Claude/Grok/DeepSeek models ([#1048](https://github.com/pipeshub-ai/pipeshub-ai/pull/1048)); OpenAI-compatible embedding endpoints; platform settings page.

### Improvements
- Indexing pipeline overhauled: reordered stages, batched embedding writes, faster Qdrant operations, more formats on the blocks schema ([#975](https://github.com/pipeshub-ai/pipeshub-ai/pull/975), [#996](https://github.com/pipeshub-ai/pipeshub-ai/pull/996)).

---

## 0.1.1-alpha — 2025-11-05

**BookStack and ServiceNow connectors arrive, SAML gets JIT provisioning** · Alpha · [`v0.1.1-alpha`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.1.1-alpha) · [Full changelog](changelog/0.1.1-alpha.md)

27 PRs one week after the founding release.

### New features
- BookStack ([#887](https://github.com/pipeshub-ai/pipeshub-ai/pull/887)) and ServiceNow Knowledge Base ([#894](https://github.com/pipeshub-ai/pipeshub-ai/pull/894)) connectors; SAML just-in-time user provisioning ([#959](https://github.com/pipeshub-ai/pipeshub-ai/pull/959)); modality-aware LLM health checks.

---

## 0.1.0-alpha — 2025-10-28

**The founding release: enterprise search platform, RAG pipeline, and a fleet of connectors from zero** · Alpha · [`v0.1.0-alpha`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.1.0-alpha) · [Full changelog](changelog/0.1.0-alpha.md)

PipesHub's first public release — nearly 700 PRs standing up the entire platform: a Node.js Express API (auth, users, knowledge bases), Python FastAPI microservices (connectors, indexing, query/RAG), a React frontend, and a stateful backbone of ArangoDB, Qdrant, MongoDB, Redis, Kafka, and etcd, shipped as self-hostable Docker Compose under Apache 2.0.

Highlights: a full RAG pipeline with token-level streaming and normalized citations; 10+ LLM and embedding providers (bring-your-own-model); 25+ connectors and datasources with an OpenAPI-driven client generator and connector registry; a unified blocks parsing schema with Docling for complex PDFs; the first agent tools and no-code agent builder; SAML SSO; and per-org knowledge bases with in-app viewers and citation highlighting.

---

*Maintained with the `release-changelog` skill (`.claude/skills/release-changelog/SKILL.md`). Detailed per-release changelogs live in [`changelog/`](changelog/).*
