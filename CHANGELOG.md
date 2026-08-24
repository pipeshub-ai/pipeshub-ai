# PipesHub Changelog

All notable changes to [PipesHub](https://github.com/pipeshub-ai/pipeshub-ai) — the workplace AI platform for enterprise search and agent workflows — documented in one place, newest first.

This file is the release registry: each entry is a condensed summary linking to the detailed per-release changelog in [`changelog/`](changelog/) (full PR-level accounting and author credits) and to the raw GitHub release. Versioning follows semver; **Stable** releases are recommended for production, **Beta/Alpha** channels preview the next stable.

| Version | Date | Channel | Theme |
|---|---|---|---|
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

Changes merged to `main` since the last release: [`v0.6.0...HEAD`](https://github.com/pipeshub-ai/pipeshub-ai/compare/v0.6.0...HEAD).

---

## 0.6.0 — 2026-08-10

**The new agent loop ships stable, and the query service gets ~1.8× faster** · Stable · [`v0.6.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.6.0) · [Full changelog](changelog/0.6.0.md)

Finalizes [0.6.0-beta](#060-beta--2026-08-01) with ~50 additional hardening PRs (~195 total since 0.5.0). The rewritten agent runtime becomes the sole execution mode, and a query-service performance overhaul roughly doubles chat throughput.

### Added
- The new agent loop is now the only agent execution mode ([#2702](https://github.com/pipeshub-ai/pipeshub-ai/pull/2702), [#2879](https://github.com/pipeshub-ai/pipeshub-ai/pull/2879)), with tuned prompts, the Slack bot migrated onto it, and its navigate/lookup tools exposed over HTTP for MCP ([#2937](https://github.com/pipeshub-ai/pipeshub-ai/pull/2937)). Reasoning-effort support runs through agents, indexing, and the OpenAPI spec.
- Omnigent integration for the PipesHub MCP server under `integrations/omnigent/` ([#2902](https://github.com/pipeshub-ai/pipeshub-ai/pull/2902)); MCP server updated to v2.2.0.
- DOCX and CSV chat attachments ([#2894](https://github.com/pipeshub-ai/pipeshub-ai/pull/2894)); bulk user invites from CSV/Excel now include a pre-send review step ([#2912](https://github.com/pipeshub-ai/pipeshub-ai/pull/2912)).

### Changed
- Query-service performance overhaul: ~1.8× throughput, p50 turn latency halved (36 s → 17.8 s), SSE traffic down 28× ([#2872](https://github.com/pipeshub-ai/pipeshub-ai/pull/2872)).

### Fixed
- Redis Streams producer no longer gets permanently poisoned after a `disconnect()` — the client is recreated on reconnect ([#2867](https://github.com/pipeshub-ai/pipeshub-ai/pull/2867)).
- OpenAI Responses API compatibility: correct token-limit truncation detection and normalization of structured responses ([#2876](https://github.com/pipeshub-ai/pipeshub-ai/pull/2876), [#2917](https://github.com/pipeshub-ai/pipeshub-ai/pull/2917)).
- GitLab connector fixes, AQL `coalesce` → `not_null`, chat markdown with `$` signs, citation popover, artifact creation, and a long tail of UI and indexing fixes.

### Breaking changes & upgrade notes
- Knowledge-base record groups migrate to connectors on upgrade ([#2650](https://github.com/pipeshub-ai/pipeshub-ai/pull/2650)).
- Parsing and extraction run as separate flag-gated services ([#2541](https://github.com/pipeshub-ai/pipeshub-ai/pull/2541), [#2734](https://github.com/pipeshub-ai/pipeshub-ai/pull/2734)).
- `GRAPH_DB_TYPE` removed; Helm chart defaults the message broker to Redis with Kafka/ZooKeeper conditional ([#2887](https://github.com/pipeshub-ai/pipeshub-ai/pull/2887)).
- Atlassian site URL removed from connector configuration ([#2693](https://github.com/pipeshub-ai/pipeshub-ai/pull/2693)); Docker image RAM limit raised ([#2853](https://github.com/pipeshub-ai/pipeshub-ai/pull/2853)).

---

## 0.6.0-beta — 2026-08-01

**New agent loop, standalone parsing & extraction services, OpenSearch arrives** · Beta · [`v0.6.0-beta`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.6.0-beta) · [Full changelog](changelog/0.6.0-beta.md) · *Finalized as [0.6.0](#060--2026-08-10)*

Roughly 140 PRs reworking two core engines: the agent runtime gets its new loop implementation, and document parsing/data extraction split out of indexing into their own services.

### Added
- New agent loop implementation ([#2702](https://github.com/pipeshub-ai/pipeshub-ai/pull/2702)).
- Standalone parsing and extraction services behind a deployment flag ([#2541](https://github.com/pipeshub-ai/pipeshub-ai/pull/2541)), with health checks, concurrency hardening, and base64-free PDF transport to Docling.
- OpenSearch as a supported search backend ([#2547](https://github.com/pipeshub-ai/pipeshub-ai/pull/2547)); web connectors rebuilt on crawl4ai ([#2641](https://github.com/pipeshub-ai/pipeshub-ai/pull/2641)).
- Bulk user invites via CSV/Excel upload ([#2715](https://github.com/pipeshub-ai/pipeshub-ai/pull/2715)); Slack personal connector ([#2801](https://github.com/pipeshub-ai/pipeshub-ai/pull/2801)); Box shared-files sync; Google Drive shared-drive and folder filters.
- Jira DC and Confluence DC agent toolsets ([#2661](https://github.com/pipeshub-ai/pipeshub-ai/pull/2661), [#2667](https://github.com/pipeshub-ai/pipeshub-ai/pull/2667)).

### Changed
- Docling is the default PDF parser with OCR fallback ([#2724](https://github.com/pipeshub-ai/pipeshub-ai/pull/2724), [#2672](https://github.com/pipeshub-ai/pipeshub-ai/pull/2672)); OCR page caps, per-call VLM-OCR instances, memory/latency optimizations, and batched table-indexing LLM calls.
- Retrieval context enriched with related records from graph edges (parent/child/attachment metadata) ([#2596](https://github.com/pipeshub-ai/pipeshub-ai/pull/2596)).

### Fixed
- MongoDB deployment stability, message-broker retry support, EMFILE file-descriptor limits, indexing-failure propagation to MD5 duplicates, reliable PPT/PPTX preview with CJK fonts, and Outlook long-lived-connection transport.

---

## 0.5.0 — 2026-07-01

**Jira & Confluence go Data Center, agents get real tools, model providers open up** · Stable · [`v0.5.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.5.0) · [Full changelog](changelog/0.5.0.md)

A large stable release: 230+ PRs since 0.4.5. Enterprise self-hosted reach, agents that act rather than just answer, and an open model layer.

### Added
- Full Jira and Confluence **Data Center** support — roles, groups, permissions, comments, attachments, audit logs, retries ([#2435](https://github.com/pipeshub-ai/pipeshub-ai/pull/2435), [#2528](https://github.com/pipeshub-ai/pipeshub-ai/pull/2528)).
- Agent action tools: Salesforce (e.g. pricebook updates), Google Drive files, upload/fetch storage, and an ask-user-question tool for mid-run clarification ([#2223](https://github.com/pipeshub-ai/pipeshub-ai/pull/2223), [#2210](https://github.com/pipeshub-ai/pipeshub-ai/pull/2210), [#2299](https://github.com/pipeshub-ai/pipeshub-ai/pull/2299), [#2238](https://github.com/pipeshub-ai/pipeshub-ai/pull/2238)).
- First-class OpenRouter, LiteLLM proxy, and LM Studio providers ([#2621](https://github.com/pipeshub-ai/pipeshub-ai/pull/2621), [#2628](https://github.com/pipeshub-ai/pipeshub-ai/pull/2628)); per-function model roles; a dedicated local embedding service ([#2466](https://github.com/pipeshub-ai/pipeshub-ai/pull/2466)).
- New parsers: MarkItDown, pdfplumber, selectolax; code-file indexing from knowledge bases; an in-app notification service over Kafka → Node.js → WebSockets ([#2431](https://github.com/pipeshub-ai/pipeshub-ai/pull/2431)).
- One-command installation script for self-hosting ([#2590](https://github.com/pipeshub-ai/pipeshub-ai/pull/2590)); Linear API-token auth.

### Changed
- Confluence content pipeline rebuilt on ADF → blocks (comments included) instead of raw HTML pass-through ([#2497](https://github.com/pipeshub-ai/pipeshub-ai/pull/2497)).
- GitLab connector matured: incremental sync, repo-tree pagination, batch code-file fetching, and a major sync speedup ([#2438](https://github.com/pipeshub-ai/pipeshub-ai/pull/2438), [#2443](https://github.com/pipeshub-ai/pipeshub-ai/pull/2443)).

### Fixed
- Memory leaks across services ([#2408](https://github.com/pipeshub-ai/pipeshub-ai/pull/2408)); new ArangoDB indexes; Qdrant delete-points API for embedding deletion; Redis deadlock on unparseable messages; etcd watcher leak; OAuth errors now surface to the frontend.

---

## 0.4.5 — 2026-05-20

**GitLab self-managed (EE) support and slimmer deployments** · Stable · [`v0.4.5`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.5) · [Full changelog](changelog/0.4.5.md)

A ~20-PR patch release.

### Added
- GitLab connector support for self-managed Enterprise Edition instances ([#2337](https://github.com/pipeshub-ai/pipeshub-ai/pull/2337)).
- Multiple authentication mechanisms per org side by side ([#2341](https://github.com/pipeshub-ai/pipeshub-ai/pull/2341)).

### Changed
- Slimmer Docker image ([#2325](https://github.com/pipeshub-ai/pipeshub-ai/pull/2325)); port 3001 exposed directly and the separate connector public URL removed ([#2326](https://github.com/pipeshub-ai/pipeshub-ai/pull/2326)).

### Fixed
- SAML JIT provisioning, Arango cleanup on connector-instance deletion, deep-agent conversation bleed, and Redis consumers dropping unparsable messages instead of retrying forever ([#2335](https://github.com/pipeshub-ai/pipeshub-ai/pull/2335)).

---

## 0.4.4 — 2026-05-18

**Jira and Confluence Data Center connectors** · Stable · [`v0.4.4`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.4) · [Full changelog](changelog/0.4.4.md)

A quick ~20-PR patch bringing on-prem Atlassian into PipesHub search.

### Added
- Confluence Data Center connector ([#2302](https://github.com/pipeshub-ai/pipeshub-ai/pull/2302)) and Jira Data Center connector ([#2303](https://github.com/pipeshub-ai/pipeshub-ai/pull/2303)).

### Changed
- Docker image optimized for size and build time ([#2304](https://github.com/pipeshub-ai/pipeshub-ai/pull/2304)).

### Fixed
- PPTX format detection, Mermaid rendering, chat image overflow, forgot-password error display, and connector-filter pruning.

---

## 0.4.3 — 2026-05-16

**Production-ready Kubernetes deploys, chat attachments, and Vertex AI** · Stable · [`v0.4.3`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.3) · [Full changelog](changelog/0.4.3.md)

First patch series on 0.4.x, ~90 PRs.

### Added
- Production-grade Helm chart: StatefulSets, horizontal pod autoscaling, Kind-based dev flow ([#2179](https://github.com/pipeshub-ai/pipeshub-ai/pull/2179)).
- Chat attachments (PDF/JPG/PNG) from the UI and Slack ([#2190](https://github.com/pipeshub-ai/pipeshub-ai/pull/2190)); Google Vertex AI provider ([#2279](https://github.com/pipeshub-ai/pipeshub-ai/pull/2279)); Exa web-search engine ([#2241](https://github.com/pipeshub-ai/pipeshub-ai/pull/2241)).
- PipesHub CLI for environment setup and configuration ([#1772](https://github.com/pipeshub-ai/pipeshub-ai/pull/1772)); SharePoint toolset.

### Changed
- Search and conversation APIs made SDK-ready with an OpenAPI spec cleaned up to match reality ([#2267](https://github.com/pipeshub-ai/pipeshub-ai/pull/2267)).

### Fixed
- Secrets masked in configuration API responses ([#2293](https://github.com/pipeshub-ai/pipeshub-ai/pull/2293)); deletion guards for models/connectors that agents depend on; proactive token refresh; knowledge-hub memory-exceed fix; broad connector, citation, and UI fixes.

---

## 0.4.0 — 2026-05-05

**Next.js frontend replaces the React UI; GitLab, Salesforce, and Zoom connectors land** · Stable · [`v0.4.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.0) · [Full changelog](changelog/0.4.0.md)

A very large release — 380+ PRs since 0.3.0.

### Added
- Coding sandbox with artifacts and an image-generation model type ([#2031](https://github.com/pipeshub-ai/pipeshub-ai/pull/2031)); speech-to-text and text-to-speech model types; web search for the assistant ([#1903](https://github.com/pipeshub-ai/pipeshub-ai/pull/1903)); deep-agent critic + reflector loop ([#1739](https://github.com/pipeshub-ai/pipeshub-ai/pull/1739)).
- Connectors: GitLab v1 ([#1857](https://github.com/pipeshub-ai/pipeshub-ai/pull/1857)), Salesforce connector + toolset ([#1787](https://github.com/pipeshub-ai/pipeshub-ai/pull/1787), [#1862](https://github.com/pipeshub-ai/pipeshub-ai/pull/1862)), Zoom connector + toolset, personal Outlook ([#1756](https://github.com/pipeshub-ai/pipeshub-ai/pull/1756)).
- First-class toolset pages plus Redshift, OneDrive, and Lumos toolsets; developer-settings page for OAuth 2.0 apps ([#1913](https://github.com/pipeshub-ai/pipeshub-ai/pull/1913)).
- Indexing reconciliation and SQL connectors ([#1568](https://github.com/pipeshub-ai/pipeshub-ai/pull/1568)); MiniMax provider; agent service accounts.
- Deployment: Redis as an optional Kafka replacement ([#1823](https://github.com/pipeshub-ai/pipeshub-ai/pull/1823)), Windows support, hot-reloading dev composes, log rotation, S3-compatible internal blob storage.

### Changed
- The React SPA was rewritten as a Next.js application ([#1882](https://github.com/pipeshub-ai/pipeshub-ai/pull/1882)).
- Word-by-word answer streaming replaces large chunks ([#2141](https://github.com/pipeshub-ai/pipeshub-ai/pull/2141)); indexing throughput improved in two rounds; config reads no longer served from cache.

### Fixed
- Reset-password hardened against account enumeration ([#2041](https://github.com/pipeshub-ai/pipeshub-ai/pull/2041)); blocked-user cooling period; ArangoDB deadlock retries; frontend OOM prevention; hundreds of UI, citation, and connector fixes; unit-test coverage pushed above 90%.

### Breaking changes & upgrade notes
- Legacy React frontend removed — Next.js is the only UI ([#2169](https://github.com/pipeshub-ai/pipeshub-ai/pull/2169)).
- Python services moved to Python 3.12 ([#1755](https://github.com/pipeshub-ai/pipeshub-ai/pull/1755)); single backend URL; SMTP step removed from onboarding; backend switched to the new custom MCP server ([#2185](https://github.com/pipeshub-ai/pipeshub-ai/pull/2185)); dev compose defaults to Neo4j.

---

## 0.4.0-beta.2 — 2026-05-02

**Next.js frontend previews; GitLab, Salesforce, and Zoom connectors arrive** · Beta · [`v0.4.0-beta.2`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.4.0-beta.2) · [Full changelog](changelog/0.4.0-beta.2.md) · *Finalized as [0.4.0](#040--2026-05-05)*

The pre-release cut of the 0.4.0 cycle (350+ PRs). Previews the Next.js frontend alongside the legacy UI (removed in stable) and carries the same connector, sandbox, web-search, and deployment additions finalized in 0.4.0 above.

---

## 0.3.0 — 2026-03-20

**Agents get toolsets and MCP, Slack becomes a real bot platform, Neo4j joins as a graph option** · Stable · [`v0.3.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.3.0) · [Full changelog](changelog/0.3.0.md)

150+ PRs; squarely an AI-platform release.

### Added
- Agent toolsets — Jira, ClickUp, MariaDB, Microsoft Teams, Outlook, GitHub — and a deep-agent mode ([#1506](https://github.com/pipeshub-ai/pipeshub-ai/pull/1506), [#1642](https://github.com/pipeshub-ai/pipeshub-ai/pull/1642)).
- MCP server bundled with the main service ([#1611](https://github.com/pipeshub-ai/pipeshub-ai/pull/1611)) on a matured OAuth2 stack (RFC 9728 resource metadata, developer settings, Cursor support).
- Neo4j as an alternative graph database alongside ArangoDB, including optional Helm support ([#1427](https://github.com/pipeshub-ai/pipeshub-ai/pull/1427)).
- Seven new datasources: RSS, ClickHouse, Lattice, Databricks, Lumos, ClickUp, personal GitHub.
- JIT SAML SSO ([#1502](https://github.com/pipeshub-ai/pipeshub-ai/pull/1502)); AWS Bedrock IAM-role credentials from EC2; OpenCV + PyMuPDF parser.

### Changed
- Slack bot: streamed answers via the chat stream API, multiple bots per deployment, multi-user threads with @mention resolution ([#1526](https://github.com/pipeshub-ai/pipeshub-ai/pull/1526), [#1538](https://github.com/pipeshub-ai/pipeshub-ai/pull/1538), [#1593](https://github.com/pipeshub-ai/pipeshub-ai/pull/1593)).
- Web connector reliability overhaul: generator-based crawling, retries, domain scoping, better HTML cleaning.

### Fixed
- Cross-connector record leakage from identical `virtualId`s ([#1699](https://github.com/pipeshub-ai/pipeshub-ai/pull/1699)); Knowledge Hub search/filter latency; async connector-instance deletion; SharePoint Graph API pagination.

### Breaking changes & upgrade notes
- Deprecated endpoints and the signed-URL download route removed ([#1470](https://github.com/pipeshub-ai/pipeshub-ai/pull/1470)); `connector_disabled`/`paused` indexing statuses removed; manual-sync connectors no longer sync on startup.

---

## 0.2.0 — 2026-02-11

**The connector fleet goes wide: 15+ new sources, multi-instance connectors, and a built-in OAuth2 provider** · Stable · [`v0.2.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.2.0) · [Full changelog](changelog/0.2.0.md)

260+ PRs turning the connector layer into a real platform.

### Added
- 15+ connectors/datasources: Jira, Box, S3, MinIO, GCS, Azure Blob, Azure Files, Nextcloud, Notion, Linear, Zammad, Zoom, dedicated Google Drive personal/enterprise, plus Monday, Snowflake, and Salesforce clients.
- Multi-instance connectors with per-instance credentials and sync filters ([#1151](https://github.com/pipeshub-ai/pipeshub-ai/pull/1151)).
- PipesHub as an OAuth2.0 provider with JIT account provisioning ([#1321](https://github.com/pipeshub-ai/pipeshub-ai/pull/1321), [#1347](https://github.com/pipeshub-ai/pipeshub-ai/pull/1347)).
- Knowledge Hub API unifying browse and search ([#1232](https://github.com/pipeshub-ai/pipeshub-ai/pull/1232)); VLM-based OCR; LLM header detection for spreadsheets; Helm chart; Kafka SASL; Redis TLS.

### Changed
- Knowledge bases re-architected as connectors (renamed "Collections") with automated migration ([#1371](https://github.com/pipeshub-ai/pipeshub-ai/pull/1371)).
- New graph-database abstraction layer: `IGraphDBProvider`, fully async ArangoDB, Node.js API's direct Arango dependency removed ([#1145](https://github.com/pipeshub-ai/pipeshub-ai/pull/1145), [#1455](https://github.com/pipeshub-ai/pipeshub-ai/pull/1455)).
- Citations overhauled: filenames, grouping, and correct numbering across streaming and overflow cases.

### Fixed / Security
- Token invalidation on logout, captcha on auth flows, user-enumeration fix, and the MongoDB "Mongobleed" CVE-2025-14847 fix ([#1252](https://github.com/pipeshub-ai/pipeshub-ai/pull/1252)).

### Breaking changes & upgrade notes
- KB → connector migration runs on upgrade; "Public connector URL" setting removed; individual account creation removed; new env vars for rate limiting and token lifetimes.

---

## 0.1.0 — 2025-12-23

**First stable release: security hardening and connector filters mature** · Stable · [`0.1.0`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/0.1.0) · [Full changelog](changelog/0.1.0.md)

Finalizes the beta and alpha series; 23 PRs on top of the beta, focused on production readiness.

### Added
- Connector filters extended to Outlook and Confluence with manually triggered filtered syncs ([#1122](https://github.com/pipeshub-ai/pipeshub-ai/pull/1122), [#1132](https://github.com/pipeshub-ai/pipeshub-ai/pull/1132)).
- SharePoint page indexing ([#1129](https://github.com/pipeshub-ai/pipeshub-ai/pull/1129)); Google Slides/Docs/Sheets on the unified blocks pipeline ([#1130](https://github.com/pipeshub-ai/pipeshub-ai/pull/1130)); Trello datasource.

### Security
- Configurable JWT expiry, stack traces stripped from API responses, stricter validation middleware ([#1153](https://github.com/pipeshub-ai/pipeshub-ai/pull/1153), [#1155](https://github.com/pipeshub-ai/pipeshub-ai/pull/1155), [#1157](https://github.com/pipeshub-ai/pipeshub-ai/pull/1157)).

---

## 0.1.0-beta — 2025-12-09

**Connector filters, five new data sources, and a security hardening sweep** · Beta · [`0.1.0-beta`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/0.1.0-beta) · [Full changelog](changelog/0.1.0-beta.md) · *Finalized as [0.1.0](#010--2025-12-23)*

40 PRs pushing toward stable.

### Added
- Connector filters scoping what gets synced ([#1058](https://github.com/pipeshub-ai/pipeshub-ai/pull/1058)); Dropbox, NextCloud, Trello, Bitbucket, and Workday data sources; MD5-checksum file version control ([#1091](https://github.com/pipeshub-ai/pipeshub-ai/pull/1091)); team management UI; Opik LLM observability.

### Fixed
- HTTP connections dying after ~a day of uptime ([#1099](https://github.com/pipeshub-ai/pipeshub-ai/pull/1099)); a sustained dependency-security sweep.

---

## 0.1.2-alpha — 2025-12-02

**Confluence and SharePoint connectors land, Azure AI models, indexing pipeline overhaul** · Alpha · [`0.1.2-alpha`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/0.1.2-alpha) · [Full changelog](changelog/0.1.2-alpha.md)

The largest point release of the alpha line (72 PRs).

### Added
- Confluence ([#1017](https://github.com/pipeshub-ai/pipeshub-ai/pull/1017)) and SharePoint ([#1018](https://github.com/pipeshub-ai/pipeshub-ai/pull/1018)) connectors; Azure-hosted OpenAI/Claude/Grok/DeepSeek models ([#1048](https://github.com/pipeshub-ai/pipeshub-ai/pull/1048)); OpenAI-compatible embedding endpoints; platform settings page.

### Changed
- Indexing pipeline overhauled: reordered stages, batched embedding writes, faster Qdrant operations, more formats on the blocks schema ([#975](https://github.com/pipeshub-ai/pipeshub-ai/pull/975), [#996](https://github.com/pipeshub-ai/pipeshub-ai/pull/996)).

---

## 0.1.1-alpha — 2025-11-05

**BookStack and ServiceNow connectors arrive, SAML gets JIT provisioning** · Alpha · [`v0.1.1-alpha`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.1.1-alpha) · [Full changelog](changelog/0.1.1-alpha.md)

27 PRs one week after the founding release.

### Added
- BookStack ([#887](https://github.com/pipeshub-ai/pipeshub-ai/pull/887)) and ServiceNow Knowledge Base ([#894](https://github.com/pipeshub-ai/pipeshub-ai/pull/894)) connectors; SAML just-in-time user provisioning ([#959](https://github.com/pipeshub-ai/pipeshub-ai/pull/959)); modality-aware LLM health checks.

---

## 0.1.0-alpha — 2025-10-28

**The founding release: enterprise search platform, RAG pipeline, and a fleet of connectors from zero** · Alpha · [`v0.1.0-alpha`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.1.0-alpha) · [Full changelog](changelog/0.1.0-alpha.md)

PipesHub's first public release — nearly 700 PRs standing up the entire platform: a Node.js Express API (auth, users, knowledge bases), Python FastAPI microservices (connectors, indexing, query/RAG), a React frontend, and a stateful backbone of ArangoDB, Qdrant, MongoDB, Redis, Kafka, and etcd, shipped as self-hostable Docker Compose under Apache 2.0.

Highlights: a full RAG pipeline with token-level streaming and normalized citations; 10+ LLM and embedding providers (bring-your-own-model); 25+ connectors and datasources with an OpenAPI-driven client generator and connector registry; a unified blocks parsing schema with Docling for complex PDFs; the first agent tools and no-code agent builder; SAML SSO; and per-org knowledge bases with in-app viewers and citation highlighting.

---

*Maintained with the `release-changelog` skill (`.claude/skills/release-changelog/SKILL.md`). Detailed per-release changelogs live in [`changelog/`](changelog/).*
