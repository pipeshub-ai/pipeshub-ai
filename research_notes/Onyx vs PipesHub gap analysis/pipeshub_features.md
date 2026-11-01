# PipesHub feature inventory (as of 1 Nov 2026, v1.0.0 + main)

Method: the code is the ground truth. The checkout at `/home/user/pipeshub-ai` is a **shallow clone** with 50 commits, the newest from 2026-11-01. `git log --since=2025-06-01` therefore shows only the last two days, so release history comes from `CHANGELOG.md` and `changelog/*.md`. **docs.pipeshub.com and pipeshub.com could not be reached:** DNS fails because both hosts are outside this session's network allow-list. Pricing and cloud-tier details are therefore unverified (see Gaps). Links below use `https://github.com/pipeshub-ai/pipeshub-ai/blob/main/<path>`, which matches the local paths.

Base URL used below: `GH = https://github.com/pipeshub-ai/pipeshub-ai/blob/main/`

## Chat & search UX

### Takeaway
PipesHub has a full chat UI. It offers four query modes (internal search, web search, image creation, agent), four agent strategies (auto, quick, plan & execute, deep), block-level citations with text-fragment deep links, a hierarchical source picker with @-mentions, attachments, voice input and output, feedback, forking, shared chats, Projects, and an artifact gallery. Persistent user memory is not exposed as a product feature. "Deep research" exists as the `deep` agent strategy; the separate deep-research control was removed from the composer in 1.0.0.

### Cited Findings
- **Positioning:** the README calls it "The Open-Source Workplace AI Platform" (Apache-2.0) and lists these features: explainable answers with "precise block citations", permission-aware search, BYO model / fully self-hostable, "50+ connectors with real-time and scheduled indexing", knowledge-graph retrieval, multimodal (image/diagram/scanned files plus voice), unified search / deep research / agents, no-code agents and actions, and artifacts and code execution in a sandbox — [README.md](GH/README.md)
- **Query modes** in the composer: "Internal search", "Web search" ("Live web search and sources"), "Image creation" ("Generate images from prompts and company data") and "Agent" ("plan and use tools"). Answer modes are Internal / Web / "Internal + web search" — [frontend/lib/i18n/locales/en-US.json](GH/frontend/lib/i18n/locales/en-US.json) (`chat.queryModes`, `chat.answerMode`)
- **Agent strategies** (the backend calls these chat modes):
  - Auto
  - Quick: flat ReAct with no planner and no intent pre-step
  - Plan & execute: `PlanCritiqueExecuteLoop` with create_plan / critique_plan / verify_result / replan tools
  - Deep: an `OrchestratorLoop` that spawns domain sub-agents

  Sources: [backend/python/app/agents/agent_loop/modes.py](GH/backend/python/app/agents/agent_loop/modes.py); `chat.agentStrategy` in en-US.json
- **Reasoning-effort selector** in chat with Low / Medium / High / Max, plus per-agent defaults — `chat.reasoningEffort` in en-US.json; [changelog/0.6.0](GH/CHANGELOG.md)
- **Chat message actions:** like/dislike feedback with categories ("Excellent answer", "Helpful citations", "Incorrect info", "Missing info", "Poor citations"…), copy as Markdown with citations or as plain text, fork / "fork from here", regenerate, edit query, read aloud (TTS), stop generating, expand tables to full view, download a table as CSV, Mermaid diagram zoom / copy / download, and "show more citations" — `chat.*` keys in en-US.json; [docs/fork-chat.md](GH/docs/fork-chat.md)
- **Chat management:** rename, pin, archive, delete and export a chat; an "Archived chats" workspace page with restore and permanent delete; a "Shared chats" / "Shared by" section in the sidebar; and a share sidebar that shares with users or teams and sets roles — en-US.json (`chat.sharedChats`, `workspace.archivedChats`, `shareSidebar`)
- **Source scoping:** since 1.0.0 the source picker is a tree of apps, collections, record groups, folders and records. It supports @-mention of nodes in the composer, and a saved agent or a project can be scoped the same way — [changelog/1.0.0.md](GH/changelog/1.0.0.md) line ~94; frontend `mention-composer-field.tsx`, `mention-menu.tsx` under [frontend/app/(main)/chat/components](GH/frontend/app/(main)/chat/components)
- **Citations:**
  - Block-level citations with filenames, grouping and correct numbering (overhauled in 0.2.0) — [CHANGELOG.md](GH/CHANGELOG.md)
  - Text-fragment deep links (`#:~:text=`) are built only by `app/utils/text_fragments/` (`build_text_fragment_url`). The package handles Unicode/CJK, markdown stripping and `-` encoding, has a golden corpus, and is verified with Playwright against Chromium. The Slack bot shares the logic (`slack-bot/src/utils/citations.ts`) — [docs/text-fragments.md](GH/docs/text-fragments.md); `frontend/playwright.text-fragments.config.ts`
  - The code parser gives correct citations into source files (0.7.0) — CHANGELOG.md
- **Source preview:** a `/record` page and a record viewer (`recordView`, `filePreview` i18n namespaces). PPT/PPTX previews render with CJK fonts (0.6.0-beta) — frontend route list; CHANGELOG.md
- **File uploads / attachments in chat:**
  - PDF, JPG and PNG from the UI and Slack (0.4.3); DOCX and CSV (0.6.0); pasted text as an attachment (0.7.0)
  - The upload limit is set in Workspace → Labs ("max. 1000" MB) — CHANGELOG.md; `workspace.labs` in en-US.json
- **Collections / Knowledge Base:**
  - Knowledge bases were re-architected as connectors and renamed "Collections" in 0.2.0. Their internal type is `kb` (`KnowledgeBaseConnector`, manual sync only) — CHANGELOG.md; [connector_factory.py](GH/backend/python/app/connectors/core/factory/connector_factory.py); [localKB/connector.py](GH/backend/python/app/connectors/sources/localKB/connector.py)
  - The Knowledge Hub API unifies browse and search with cursor paging (1.0.0) — CHANGELOG.md
  - Trash / "Recently deleted" with restore and retention purge, behind Labs `ENABLE_SOFT_DELETE` (0.10.0) — route `/knowledge-base/recently-deleted`; CHANGELOG.md
- **Projects** (0.9.0, flag `ENABLE_PROJECTS`): a shared workspace for chats with members, its own knowledge base and instructions, plus pin and archive — CHANGELOG.md; `projects` in en-US.json
- **Artifacts:** files that agents create in chat (Image, Chart, Document, Spreadsheet…) appear in an "All artifacts (Beta)" gallery (0.10.0) and in a chat artifacts panel. A coding sandbox generates reports, charts and dashboards (0.4.0) — `artifacts` in en-US.json; CHANGELOG.md
- **Web search:** an admin page under Workspace → Web search, where one provider is active at a time, with a "Send images to LLM" toggle. Providers in code: Brave, ddgs, DuckDuckGo HTML, Exa, Firecrawl, SearXNG, Serper and Tavily. Optional compose profiles add `searxng` and `crawl4ai` for page reading — [backend/python/app/web/search/](GH/backend/python/app/web/search); [docs/web-search.md](GH/docs/web-search.md); [deployment/docker-compose/docker-compose.yml](GH/deployment/docker-compose/docker-compose.yml)
- **Voice:** mic input (STT) and read-aloud (TTS) use the STT/TTS model types added in 0.4.0. STT providers are LiteLLM proxy, OpenAI, OpenAI-compatible, Whisper, Wispr, Gemini and OpenRouter. TTS providers are LiteLLM proxy, OpenAI, OpenAI-compatible, Gemini and OpenRouter — [backend/python/app/utils/aimodels.py](GH/backend/python/app/utils/aimodels.py)
- **Image generation** is a model type. Providers are LiteLLM proxy, OpenAI, OpenAI-compatible, Gemini and OpenRouter — aimodels.py
- **Retrieval-side chat features in 1.0.0:**
  - "focused reading" of the relevant part of a record
  - period-by-period timeline search, automatic for spans over a month
  - incoming and outgoing record references in chat context
  - named-entity search behind feature switches

  Source: [changelog/1.0.0.md](GH/changelog/1.0.0.md)
- **Admin prompt control:** a custom org-wide system prompt (Workspace → Prompts) — `workspace.prompts` in en-US.json
- **i18n:** the UI ships 10 locales: cs-CZ, de-DE, en-IN, en-US, es-ES, hi-IN, ko-KR, zh-CN, zh-SG and zh-TW. The README is translated into 13 languages (fr, de, zh-CN, ja, ru, he, ko, es, pt, tr, vi, it, cs) — [frontend/lib/i18n/locales/](GH/frontend/lib/i18n/locales); [docs/i18n/](GH/docs/i18n)
- **Answer streaming:** word-by-word streaming (0.4.0); SSE traffic was cut 28× in 0.6.0; there is a non-streaming chat API — CHANGELOG.md; [docs/non-streaming-chat.md](GH/docs/non-streaming-chat.md)

### Inferences
- Memory is not a product feature. A `memory_tools.py` builtin and SQLite/base memory providers exist in `backend/python/app/agent_loop_lib/`, but a grep found no import of them under `app/agents/`. Persistent per-user memory is therefore likely not exposed.
- Deep research is a mode (`deep`) rather than a separate report-generation product. The changelog says "the deep-research control leaves the chat composer" (1.0.0).

### Gaps
- Public chat-share links (anonymous or by URL) versus share-with-user/team only. Only user/team sharing was confirmed in i18n.
- Exact file-type list for chat uploads in the current UI (only the changelog list above).

## Agents & actions

### Takeaway
PipesHub has a visual canvas agent builder and a large first-party toolset catalog (~40 action packages). It has MCP client support with a 31-server catalog and OAuth/DCR, custom REST actions from OpenAPI specs, skills, tool approvals, a code sandbox, and service agents. A workflow builder exists only as a feature flag, and no scheduled-agent feature was found.

### Cited Findings
- **Agent builder:** a node-graph canvas with a palette (drag/tap). Nodes are Chat input, Model, Knowledge (apps / collections), Toolsets, Tools, MCP servers, Skills and Chat output. Each agent has a system prompt, a start message, a default reasoning effort and "Send user context".
  - Agent types: "User agent" vs "Service agent". A service agent is shared org-wide, has agent-level toolsets and searches with the creator's or a service account's access, and conversion is irreversible.
  - Other options: "Share with org" and "Use as default".

  Sources: `agentBuilder.*` in [en-US.json](GH/frontend/lib/i18n/locales/en-US.json); routes `/agents/new`, `/agents/edit`
- **Per-agent model choice:** the agent's Model node, which falls back to "Using org default" — `agentBuilder.connectModel`, `usingOrgDefault`
- **First-party action toolsets** under [backend/python/app/agents/actions/](GH/backend/python/app/agents/actions):
  - Apps: airtable, azure, bookstack, box, clickup, confluence, confluence_data_center, discord, dropbox, evernote, freshdesk, github, gitlab, jira, jira_data_center, linear, linkedin, lumos, mariadb, notion, posthog, redshift, s3, salesforce, slack, zendesk, zoom
  - Google: calendar, docs, drive, gmail, sheets, youtube, enterprise
  - Microsoft: one_drive, one_note, outlook, sharepoint, teams
  - Internal: artifacts, calculator, code_graph, coding_sandbox, database_sandbox, image_generator, internal_tools, knowledge_graph, knowledge_hub, retrieval, storage_search
  - Actions are gated by `ENABLE_ACTIONS` (0.7.0) — CHANGELOG.md
- **Custom API actions (1.0.0):** connect REST APIs from an OpenAPI spec, including personal actions and actions connected from chat. The flag is `ENABLE_API_TOOLSETS` — [backend/python/app/agents/openapi/](GH/backend/python/app/agents/openapi); [docs/api-toolsets.md](GH/docs/api-toolsets.md); changelog/1.0.0.md
- **MCP client:** [backend/python/app/agents/mcp/](GH/backend/python/app/agents/mcp) has dcr.py, cimd.py, oauth_client.py, step_up.py, token_refresh.py, stdio_policy.py and url_guard.py.
  - The built-in catalog in `mcp/servers/` has 31 servers: Atlassian Rovo, Box, Brave Search, ClickUp, Context7, Datadog, DocuSign, Dropbox, Exa, GitHub, GitLab, Gmail, Google Calendar, Google Drive, Greenhouse, HubSpot, Hugging Face, Linear, Microsoft Learn, Notion, Outreach, PagerDuty, PostHog, Redshift, Sentry, ServiceNow, Slack, Slack Official, Stripe and Zoom.
  - UI pages are Workspace → MCP servers (team / personal).
  - Custom STDIO servers require `MCP_ALLOW_CUSTOM_STDIO=true` (0.10.0). MCP servers are on by default from 1.0.0 — CHANGELOG.md
- **Tool approvals:** per-person approvals with Allow / Deny / "Don't ask again for this tool" and an expiry. **Organization-wide approval rules and per-agent approvals are Enterprise Edition only** ("Tool approvals per agent are part of the Enterprise Edition") — `chat.toolApproval`, `agentBuilder.mcpAgentApprovalsEnterprise`, `workspace.mcpServers.enterprise.orgRulesTitle` in en-US.json; [mcp-tools-approvals-tab.tsx](GH/frontend/app/(main)/workspace/mcp-servers/team/components/mcp-tools-approvals-tab.tsx)
- **Admin review of people's own MCP servers and actions** ("People's own MCP servers / actions") is an EE notice in OSS — [mcp-catalog-layout.tsx](GH/frontend/app/(main)/workspace/mcp-servers/team/components/mcp-catalog-layout.tsx); [actionsTeamPage.tsx](GH/frontend/app/(main)/workspace/actions/team/actionsTeamPage.tsx)
- **Skills:** "markdown instructions, optional scripts and reference files, versioned and governed". They can be imported from npm, a URL or a zip, and have Active / Deprecated / Candidate / Disabled status and built-in packs. Flag `ENABLE_SKILLS` — `workspace.skills` in en-US.json; [backend/python/app/agents/agent_loop/skills/](GH/backend/python/app/agents/agent_loop/skills)
- **Ask-user-question tool** for mid-run clarification (0.5.0) — CHANGELOG.md
- **Code sandbox:** Docker reached through a policy proxy, network off by default (`SANDBOX_MODE`, `SANDBOX_ALLOW_NETWORK`), secret masking in output — CHANGELOG.md 0.10.0; commit #5101
- **Agent loop internals:**
  - Shipped in 0.6.0; model-sized context budgets, compaction and provider prompt caching came in 0.10.0 and 1.0.0
  - Prompt-injection guard on sub-agent execution (0.7.0)

  Sources: CHANGELOG.md; [docs/context-engineering.md](GH/docs/context-engineering.md); [docs/multi-agent-best-practices.md](GH/docs/multi-agent-best-practices.md)
- **Workflows:** only the flag `ENABLE_WORKFLOW_BUILDER` exists, in [backend/python/app/services/featureflag/](GH/backend/python/app/services/featureflag/featureflag.py) and the Node config validator. No workflow UI route exists in `frontend/app` — frontend route list
- **Scheduled agents:** a grep for `scheduled_agent`, `scheduledAgent` and `agent_schedule` across backend and frontend returned 0 matches.

### Inferences
- Workflow builder: flag-only or stubbed in OSS, with no shipped UI.
- Scheduled or triggered agents: absent from OSS as of this commit.
- The agent stack is the most heavily engineered area: three loop strategies, domain sub-agents, context engineering docs and evals in `agent_loop/evals`.

### Gaps
- Whether the EE build adds workflows or schedules. EE source is not in this repo.

## MCP server, API, SDKs & surfaces

### Takeaway
PipesHub exposes an MCP server at `{origin}/mcp`, secured with OAuth 2.1, PATs or the device flow, plus a 399-path OpenAPI REST API and SDKs in Python, TypeScript and Go. Other surfaces are a Slack bot, an Electron desktop app with local-folder sync, and a hosted cloud. Discord, Telegram and GitHub bots are "coming soon" stubs. No browser extension, Teams bot, embeddable widget or mobile app was found.

### Cited Findings
- **MCP server:** mounted at `/mcp` in Express via the `@pipeshub-ai/mcp` 4.8.0 package. It advertises RFC 9728 `/.well-known/oauth-protected-resource/mcp`.
  - Tools seen in the Node module: `pipeshub_search`, `pipeshub_chat`, and the code-graph tools `pipeshub_code_search`, `_semantic_search`, `_symbols`, `_outline`, `_read`, `_neighbours`, `_path`, `_impact`, `_entry_points`, `_repos`. The code-graph tools need the `code:read` scope.
  - Navigate and lookup tools are exposed over HTTP for MCP (0.6.0).

  Sources: [backend/nodejs/apps/src/modules/mcp/](GH/backend/nodejs/apps/src/modules/mcp/routes/mcp.routes.ts); [code-graph.tools.ts](GH/backend/nodejs/apps/src/modules/mcp/code-graph.tools.ts); backend/nodejs/apps/package.json; CHANGELOG.md
- **Cloud MCP:** "Coding agents connect to `https://app.pipeshub.com/mcp` with a personal access token" — [README.md](GH/README.md)
- **Auth for API/MCP:**
  - PATs: `phpat_` prefix, scoped and revocable, any member can mint them, 30/90/365-day or no expiry
  - PipesHub as an OAuth 2.0 provider (0.2.0) with PKCE S256 only, granular consent and single-use auth codes (1.0.0)
  - Device-code login for coding agents (0.9.0)
  - Service accounts and service tokens (0.9.0); `client_credentials` must name a service account (1.0.0)

  Sources: CHANGELOG.md; README.md; routes `/oauth/device`, `/workspace/developer-settings/{oauth2,personal-access-tokens}`, `/workspace/service-accounts`
- **REST API:** [pipeshub-openapi.yaml](GH/backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml) has 399 top-level paths. `POST /api/v1/retrieve` arrived in 1.0.0, and an OpenAI-compatible `GET /v1/models` in 0.10.0. The API reference opens from Developer settings (#5091) — CHANGELOG.md; git log
- **SDKs:** Python `pipeshub-sdk` (PyPI), TypeScript `@pipeshub-ai/sdk` (npm), Go `pipeshub-sdk-go`. SDK 1.7.0 shipped in 0.9.1 — README.md; CHANGELOG.md
- **Examples and integrations:** [pipeshub-ai/examples](https://github.com/pipeshub-ai/examples) (company-knowledge MCP, search box, SDK starters); Omnigent integration in [integrations/omnigent/](GH/integrations/omnigent) — README.md
- **CLI:** "PipesHub CLI for environment setup and configuration" (0.4.3) — CHANGELOG.md
- **Slack bot:**
  - Code lives in [backend/nodejs/apps/src/integrations/slack-bot/](GH/backend/nodejs/apps/src/integrations/slack-bot) (botRegistry, receiver, slackApp)
  - Streamed answers, multiple bots per deployment, multi-user threads with @mention resolution (0.3.0); runs on the new agent loop (0.6.0); attachments (0.4.3)
  - Workspace → Bots maps each bot to an agent

  Sources: CHANGELOG.md; `workspace.bots` in en-US.json
- **Other bots:** Discord, Telegram and GitHub bot types are defined but `enabled: false` with "coming soon" — [bot-config-panel.tsx](GH/frontend/app/(main)/workspace/bots/components/bot-config-panel.tsx) lines 156-160
- **Electron desktop app:** in [frontend/electron/](GH/frontend/electron) (main.ts, deep-link.ts, `local-sync/` with watcher, transport and persistence). There is a "Connect to PipesHub server" URL setup screen, and builds come from `electron-builder.yml`. A Local FS connector (`localfs`) pairs with it — en-US.json `electron`; connector_factory.py
- **Teams:** an MS Teams *agent toolset* exists (`actions/microsoft/teams`, 0.3.0), but no Teams bot. A grep for msteams, teams-bot and teamsBot returned 0 hits.
- **Browser extension:** a grep for browser.extension and chrome.extension returned 0 hits.
- **Embeddable widget:** no widget product was found. The 23 "widget" hits are UI internals, by inspection of names.

### Inferences
- A mobile-responsive web UI exists (`useIsMobile`, `MOBILE_PAGE_PADDING_TOP` in pages), but no native mobile app was found.

### Gaps
- The full MCP tool list from the `@pipeshub-ai/mcp` package. The package source is not in the repo, and docs.pipeshub.com/for-agents was unreachable.

## Connectors

### Takeaway
The code registers **48 connector types** in `ConnectorFactory._connector_registry`, including the Collections `kb` type and a `demo` connector. There are 50 `connector.py` files, and CIFS is deliberately unregistered. Six more are flag-gated beta stubs: Google Calendar, Meet, Forms, Slides, Docs and Airtable. Most connectors are scheduled (60 min default) or manual. Real-time/webhook sync exists only for Google Drive and Gmail (both editions), Box and Dropbox. Per-record ACL sync is the default model, but object stores, SQL databases and personal connectors are "app-level" (all-or-nothing). The README's "50+" count mixes in agent toolsets and datasources.

### Cited Findings
- **Registry** in [connector_factory.py](GH/backend/python/app/connectors/core/factory/connector_factory.py):

  | Group | Registry keys |
  | --- | --- |
  | Microsoft | onedrive, sharepointonline, outlook, outlookpersonal |
  | Atlassian | confluence, confluencedatacenter, confluencedatacenterpersonal, jira, jiracloudpersonal, jiradatacenter, jiradatacenterpersonal |
  | Google | drive, driveworkspace, gmail, gmailworkspace |
  | File and cloud storage | box, dropbox, dropboxpersonal, nextcloud, s3, minio, gcs, azureblob, azurefiles, smb |
  | Wiki, ticketing, CRM | servicenow, bookstack, drupalwiki, linear, notion, notionpersonal, zammad, zendesk, salesforce, zoom |
  | Code | github, githubteams, gitlab, gitlabpersonal |
  | Databases | postgresql, mariadb, snowflake |
  | Chat | slackworkspace, slack |
  | Other | web, rss, localfs, demo, kb |

  The file comments: "CIFS stays out of the registry until SMB1 is tested against a real server."
- **Beta connectors** (`_beta_connector_definitions`, registered only under `ENABLE_BETA_CONNECTORS`): calendar, meet, forms, slides, docs, airtable. They are stub classes whose `connect()` only prints "Connecting to …" — [backend/python/app/connectors/core/registry/connector.py](GH/backend/python/app/connectors/core/registry/connector.py) lines 63-545
- **Sync strategies:** nearly every connector declares `[SCHEDULED, MANUAL]` with `with_scheduled_config(True, 60)`. Postgres and MariaDB use 120 min, and Collections (`localKB`) is manual only.
  - `with_realtime_support(True)`: Google Drive individual and team, Gmail individual and team, Box, Dropbox personal
  - Webhook events: Box (FILE.UPLOADED/DELETED/MOVED, FOLDER.CREATED, COLLABORATION.*) and Dropbox (file.added/modified/deleted)
  - Explicitly not realtime: Jira, Confluence (all editions), GitHub, GitLab, Linear, Notion, ServiceNow, Slack, Nextcloud, RSS, Drupal Wiki

  Source: `grep with_realtime_support|with_webhook_config` across [connectors/sources/](GH/backend/python/app/connectors/sources)
- **Permission model:**
  - `PermissionModel.APP_LEVEL` ("reaching the app implies reaching every record it syncs") vs `RECORD_LEVEL` ("the source has real per-record ACLs … the default") — [backend/python/app/config/constants/arangodb.py](GH/backend/python/app/config/constants/arangodb.py) lines 101-107
  - APP_LEVEL declared by: personal Atlassian (×3), azure_blob, azure_files, cifs, dropbox_individual, github (personal), gitlab_personal, Google individual Drive and Gmail, gcs, localKB, local_fs, mariadb, a Microsoft personal connector, minio, nextcloud, notion_personal, postgres, rss, s3, slack (individual), smb, snowflake, web
  - Every other connector keeps the RECORD_LEVEL default: Jira/Confluence Cloud and DC, Drive and Gmail Workspace, SharePoint, OneDrive, Box, Dropbox team, GitHub Teams, GitLab, Linear, Notion, Salesforce, ServiceNow, Slack Workspace, Zammad, Zendesk, Zoom, BookStack, Drupal Wiki.

  Source: `grep with_permission_model` in connectors/sources
- **OSS warning on team connectors:** the UI warns that some team connectors apply no source access rules "in this edition", so everything they sync "is searchable by the whole organization". The affected connectors are S3, Azure Blob, Azure Files, GCS, MinIO, PostgreSQL and MariaDB — [team-visibility-warning.ts](GH/frontend/app/(main)/workspace/connectors/utils/team-visibility-warning.ts); `config.ts` comment "(OSS: no source permissions applied)"
- **Permission fidelity work in 1.0.0:**
  - One access check decides search, record open, the Knowledge Hub and agent tools.
  - Jira issue-security levels are enforced, and SharePoint/OneDrive sharing is fixed.
  - An audit of nearly every connector fixed pagination, deletion and access sync.
  - Source deletions propagate in nine connector families (0.10.0).

  Source: [changelog/1.0.0.md](GH/changelog/1.0.0.md); CHANGELOG.md
- **Connector chronology:**
  - Data Center editions: Jira/Confluence DC (0.4.4/0.5.0); GitLab self-managed EE (0.4.5)
  - 0.2.0: multi-instance connectors with per-instance credentials and sync filters
  - 0.3.0: datasources RSS, ClickHouse, Lattice, Databricks, Lumos, ClickUp and personal GitHub
  - 0.7.0: HackerNews datasource
  - 0.9.0: SMB 2/3, CIFS and Drupal Wiki
  - 1.0.0: Zendesk
  - Each GitHub or GitLab instance syncs exactly one repository (0.9.0)

  Source: CHANGELOG.md
- **Connector admin UI:** Workspace → Connectors (team / personal) with an instance panel (overview, configure, authenticate tabs, OAuth app selector), filters, reindex scope, force-reindex and "reindex blocked" dialogs. The Services page shows infra and app health. A Labs "Vector store" action deletes all embeddings or re-embeds every record — frontend routes; `reindexScope`, `forceReindexConfirm`, `workspace.services`, `workspace.labs.vectorStore` in en-US.json
- **Scheduling fairness:** connectors get the least-loaded Redis Streams indexing lane (0.8.0/0.10.0), and interrupted full syncs resume (1.0.0) — CHANGELOG.md
- **Connector dev kit:** [CONNECTOR.md](GH/CONNECTOR.md), [CONNECTOR_CHECKLIST.md](GH/CONNECTOR_CHECKLIST.md), [docs/connectors/](GH/docs/connectors) (HLD, LLD, permissions, testing), the `add-connector` skill and simulators (`docker-compose.integration.sharepoint-sim.yml`, `bookstack-sim`) — repo tree

### Inferences
- Counting each listed edition separately (cloud vs DC, personal vs team), there are ~46 user-visible source connectors. There are ~33 distinct source products.
- The "50+" marketing count likely includes toolsets and datasources, or the beta stubs.
- Real-time coverage is narrow. For most sources, freshness comes from 60-minute polling.
- In OSS, object stores and SQL databases have no ACL sync, while major SaaS sources (Google/Microsoft workspace editions, Atlassian, Slack, Box, Salesforce, etc.) do. Whether EE adds ACLs for the APP_LEVEL team connectors is implied by "this edition", but not confirmed.

### Gaps
- The frontend connector registry was not separately enumerated; it is driven by the backend registry via API.
- Per-connector maturity scores. No explicit beta flag exists on registered connectors, but CIFS is held back and the six Google/Airtable beta connectors are stubs.

## Retrieval / indexing

### Takeaway
PipesHub runs a microservice pipeline: connectors → indexing → parsing / Docling → extraction (classification, NER) → embedding → graph + vector stores. Search is hybrid (dense plus BM25/sparse with fusion), with a knowledge graph for relations and entities, a code graph, and multimodal embeddings. A cross-encoder reranker is not used; the unused reranker service was removed on 2026-11-01.

### Cited Findings
- **Services:** connectors :8088, indexing :8091, query :8000, docling :8081, embedding :8002, parsing :8092, extraction :8093 (`BlocksContainer` → `SemanticMetadata`) — [AGENTS.md](GH/AGENTS.md); [docs/indexing-service.md](GH/docs/indexing-service.md)
- **Graph store:** Neo4j (the default in installer and Helm) or ArangoDB via `IGraphDBProvider`. The 1.0.0 one-way graph migration reworked permissions — AGENTS.md; CHANGELOG.md
- **Vector stores:** Qdrant (default), OpenSearch (0.6.0-beta) and Redis, via `VectorDBProviderFactory` — [backend/python/app/services/vector_db/](GH/backend/python/app/services/vector_db) (`qdrant/`, `opensearch/`, `redis/`, `sparse_embeddings.py`, `strategies/`); `VECTOR_COLLECTION_STRATEGY` (0.8.0)
- **Hybrid search:** `HybridSearchRequest`, `FusionMethod` and `SparseEmbedder` in [retrieval_service.py](GH/backend/python/app/modules/retrieval/retrieval_service.py). The retrieval module also has `lexical_search.py`, `result_merging.py`, `record_cut.py`, `source_diversity.py` and entity filters. BM25 applies IDF (0.9.0). "Deeper fusion, record-level result cuts" arrived in 1.0.0 — [docs/search-record-cut.md](GH/docs/search-record-cut.md); [docs/retrieval-context.md](GH/docs/retrieval-context.md)
- **Reranking:** commit "fix(query): remove the unused reranker service, so the query service no longer loads sentence-transformers at import (#5162)", 2026-11-01 — git log
- **Knowledge graph:**
  - Graph enrichment adds related records (parent, child and attachment edges) to context, with permission checks (0.6.0-beta/0.9.1)
  - Agents get knowledge-graph entity tools with a permission-checked entity index (0.10.0)
  - Named-entity extraction, linking and entity search; cross-connector links between Slack messages, PRs, tickets and pages (1.0.0)

  Sources: CHANGELOG.md; [docs/entity-resolution.md](GH/docs/entity-resolution.md); [docs/named-entity-extraction.md](GH/docs/named-entity-extraction.md)
- **Code graph** (1.0.0): repositories are indexed into a navigable graph with exploration and impact-analysis tools — `backend/python/app/modules/code_graph/`; changelog/1.0.0.md
- **Staged indexing:** classification and NER run as separate `pipeline.classify` and `pipeline.entities` stages after a record is already searchable (1.0.0). Classify-on-first-use is a Labs flag (#5021) — CHANGELOG.md; git log
- **Parsers:**
  - Formats: PDF, DOCX, PPTX, Excel/CSV, HTML, Markdown, JSON, YAML, SQL, EPUB, images and code — [backend/python/app/modules/parsers/](GH/backend/python/app/modules/parsers)
  - PDF stack: Docling (the default PDF parser, with OCR fallback), pdfplumber + OpenCV layout analysis, PyMuPDF and VLM-OCR; MarkItDown and selectolax for other formats — `parsers/pdf/`; CHANGELOG.md 0.3.0/0.5.0/0.6.0-beta
  - OCR providers: Azure Document Intelligence and VLM-OCR — [config/constants/ai_models.py](GH/backend/python/app/config/constants/ai_models.py)
  - LLM header detection for spreadsheets; batched table-indexing LLM calls — CHANGELOG.md
- **Multimodal:** multimodal embeddings (0.7.0, `ENABLE_MULTIMODAL_MODELS`), image and diagram understanding, VLM OCR — CHANGELOG.md; featureflag constants
- **Embedding providers:** anthropic, bedrock, azureAI, azureOpenAI, cohere, default, fireworks, gemini, huggingFace, jinaAI, litellmProxy, lmStudio, mistral, ollama, openAI, openAICompatible, openRouter, sentenceTransformers, together, vertexAI and voyage. A local embedding service on :8002 is bundled in the full image (~1.3 GB) — [aimodels.py](GH/backend/python/app/utils/aimodels.py); [ADVANCED_DEPLOYMENT.md](GH/deployment/docker-compose/ADVANCED_DEPLOYMENT.md)
- **Throughput and robustness:**
  - A resource governor and fair-scheduling lanes; lane pools (#5020)
  - Records no longer stuck in QUEUED; an outbox for domain events
  - MD5 dedup per org
  - Query service ~1.8× throughput (0.6.0); p50 turn 34.4 s → 27.9 s (0.7.0)

  Sources: CHANGELOG.md; [docs/indexing-service.md](GH/docs/indexing-service.md)

### Inferences
- Ranking relies on fusion plus record cuts, not on a learned reranker. Competitors that rerank by default may beat PipesHub on precision; this is an untested hypothesis.

### Gaps
- Chunking strategy specifics (block-based via `BlocksContainer`, `text_splitting.py`). Chunk sizes were not read.

## LLM support

### Takeaway
PipesHub supports 19 LLM providers, including local (Ollama, LM Studio, OpenAI-compatible) and gateway (LiteLLM proxy, OpenRouter) options. It also has per-function model roles, provider model discovery, an OpenAI-compatible model catalog, per-agent model choice, reasoning effort, prompt caching, and identity forwarding to LLM gateways (signed-JWT format is EE).

### Cited Findings
- **LLMProvider enum:** anthropic, bedrock, azureAI, azureOpenAI, cohere, fireworks, gemini, groq, litellmProxy, lmStudio, minimax, mistral, ollama, openAI, openAICompatible, openRouter, together, vertexAI and xai — [aimodels.py](GH/backend/python/app/utils/aimodels.py) line 372
- **Model types:** llm, embedding, ocr, slm, reasoning, multiModal, imageGeneration, tts and stt ("per-function model roles", 0.5.0) — aimodels.py line 112; CHANGELOG.md
- **Discovery:** discover models from a provider and add several at a time, and list configured models at `GET /v1/models` (0.10.0). Bedrock with IAM-role credentials (0.3.0/0.7.0) — CHANGELOG.md; [docs/ai-models.md](GH/docs/ai-models.md)
- **LLM gateway identity:** forwards the user and their groups to an LLM gateway. The "Signed token (enterprise)" format is EE — [docs/llm-gateway-identity.md](GH/docs/llm-gateway-identity.md); `workspace.aiModels.identity.formatSignedJwt` in en-US.json; [identity-forwarding-dialog.tsx](GH/frontend/app/(main)/workspace/ai-models/components/identity-forwarding-dialog.tsx)
- **OpenAI Responses API compatibility** (0.6.0) and optional direct provider transports (0.7.0) — CHANGELOG.md
- **Chat model menu:** a model selector in the composer (`chat.models`, `configuredModels`). The OSS edition has no extra chat model menu entries ("Chat model menu entries … (OSS: none)") — [frontend/config.ts](GH/frontend/config.ts)

### Gaps
- None material.

## Admin & enterprise; open-source vs Enterprise Edition

### Takeaway
The OSS edition includes password, email OTP, Google, Microsoft and GitHub sign-in, roles (admin capped at 5 per org), teams, PATs, OAuth apps, service accounts, a services health page, Labs feature flags, branding defaults and SMTP config. **SAML SSO and generic OIDC were removed from OSS in 1.0.0.** User groups and group permissions, org-wide tool approval rules, per-agent approvals, admin review of personal MCP servers and actions, service-account delegation, signed-JWT gateway identity, multi-tenant "Tenants/Orgs" admin and OSS-less custom branding are Enterprise Edition (separate, closed codebase). No SCIM, audit-log UI or analytics dashboard was found in OSS.

### Cited Findings
- **Sign-in:**
  - 1.0.0: "SAML single sign-on and generic OAuth/OIDC sign-in are removed, and Azure AD sign-in is now Microsoft sign-in. Password, email OTP, Google and Microsoft sign-in stay" — [changelog/1.0.0.md](GH/changelog/1.0.0.md) line 15
  - A GitHub callback route exists (`/auth/github/callback`), but `GithubAuthCallbackPage`, `SamlSsoSuccessPage` and `OAuthAuthCallbackPage` map to `notFound` in OSS [config-pages.ts](GH/frontend/config-pages.ts)
  - Earlier releases had JIT SAML (0.3.0/0.4.5) and multiple auth methods per org (0.4.5) — CHANGELOG.md
- **Edition mechanism:**
  - [frontend/config.ts](GH/frontend/config.ts) and [config-pages.ts](GH/frontend/config-pages.ts) are edition extension points ("OSS: nothing", "OSS: PipesHub defaults").
  - In OSS, `TenantsPage`, `TenantsUsersPage`, `TenantsGroupsPage`, `OrgsPage`, `OrgsUsersPage`, `OrgsGroupsPage`, `OrgsFeatureFlagsPage`, `OAuthAppsPage` and `GroupsPermissionsPage` are `notFound`.
  - `GroupsPage` is `GroupsEnterprisePlaceholder` ("Group permissions are available in the Enterprise Edition").
  - The badge text is "Available in EE" / "Part of the PipesHub Enterprise Edition".

  Sources: [enterprise-badge.tsx](GH/frontend/app/components/ui/enterprise-badge.tsx); [groupsEnterprisePlaceholder.tsx](GH/frontend/app/(main)/workspace/groups/groupsEnterprisePlaceholder.tsx); en-US.json `common.enterprise`
- **Service-account delegation is EE:** "Acting on behalf of a person is an Enterprise capability … the allow-list, the five-minute token issued in that person's name, and the record of every attempt — is part of the Enterprise edition" — en-US.json `workspace.serviceAccounts.delegations`; [docs/service-account-delegation.md](GH/docs/service-account-delegation.md)
- **Users page:** "Groups are an Enterprise Edition feature — hide the filter in OSS" — [workspace/users/page.tsx](GH/frontend/app/(main)/workspace/users/page.tsx) line 47
- **RBAC:**
  - Admin privilege moved from admin user groups to `User.role` (0.7.0), and each org is limited to 5 admins (0.9.0)
  - Teams (Workspace → Teams), used for sharing; "Access permissions coming soon" on teams
  - Collection roles: `ORGANIZER`/`FILEORGANIZER` now read as `READER` (1.0.0)

  Sources: CHANGELOG.md; `workspace.teams` in en-US.json
- **User management:**
  - Bulk invites from CSV/Excel with a review step (0.6.0); invite emails queued through the broker (0.10.0)
  - "Requests + Blocked Users" header actions
  - A blocked-user cooling period and captcha (0.2.0/0.4.0)

  Sources: CHANGELOG.md; config.ts
- **Branding:** Workspace → General has a company logo, display name, address and a data-collection toggle. `BrandProvider` uses "OSS: PipesHub defaults" — `workspace.general`; config.ts
- **Labs and feature flags** (Workspace → Labs): the file upload limit and the flags:
  - `ENABLE_ACTIONS`, `ENABLE_API_TOOLSETS`, `ENABLE_MCP`
  - `ENABLE_ARTIFACT_READ`, `ENABLE_ARTIFACT_WRITE`
  - `ENABLE_BETA_CONNECTORS`, `ENABLE_CLASSIFY_ON_FIRST_USE`, `ENABLE_KH_SCOPE_LISTING`
  - `ENABLE_MULTIMODAL_MODELS`, `ENABLE_NAMED_ENTITY_EXTRACTION`
  - `ENABLE_PROJECTS`, `ENABLE_SKILLS`, `ENABLE_SOFT_DELETE`
  - `ENABLE_USER_CONTEXT`, `ENABLE_UTC`, `ENABLE_VECTOR_STORE_REBUILD`, `ENABLE_WORKFLOW_BUILDER`

  Sources: [featureflag.py](GH/backend/python/app/services/featureflag/featureflag.py); `workspace.labs.featureFlags`
- **Observability for admins:** a Services health page for Redis, MongoDB, the broker, etcd and the Python services — `workspace.services` in en-US.json
- **Audit logs:** `audit_log` hits are limited to the Arango schema, sandbox/agent-loop middleware logging, and the Jira/Confluence DC connectors ingesting source audit logs (0.5.0). No admin audit-log UI route exists in `frontend/app` — grep; frontend route list
- **Analytics:** no usage or analytics dashboard route. The `analytics` hits are PostHog/Redshift toolsets and code_graph internals — grep
- **SCIM:** one file matched `scim`, and it is not a SCIM endpoint (inspection of grep hits) — grep
- **Security hardening** (selected): org and connected-app isolation (1.0.0); three GHSA fixes (0.10.0); Redis TLS enforced (0.8.0); Mongo CVE-2025-14847 fix (0.2.0); [SECURITY.md](GH/SECURITY.md)

### Inferences
- Multi-tenancy: the Tenants/Orgs admin pages are EE-only. OSS is effectively single-org per deployment, although the data model is org-scoped.
- Enterprise SSO in OSS: dropping SAML/OIDC in 1.0.0 leaves OSS without enterprise SSO other than Google/Microsoft OAuth. EE likely carries it; this is unconfirmed.

### Gaps
- EE feature list, pricing, compliance certifications (SOC 2 etc.) and the Cloud plan. pipeshub.com was unreachable, and a web search found no PipesHub pricing page; third-party listings describe it only as free and Apache-2.0 ([alternativeto.net](https://alternativeto.net/software/pipeshub/about/), [dev.co](https://dev.co/ai/rag/pipeshub-ai)).

## Deployment

### Takeaway
PipesHub deploys with a one-command Docker Compose installer (slim / full / eval), a production Helm chart (StatefulSets, HPA, PDB, NetworkPolicy, EKS values), or the hosted PipesHub Cloud. Required infra is MongoDB, a graph DB (Neo4j default or Arango), Qdrant, Redis, and optionally Kafka + ZooKeeper and etcd. The coding sandbox and web search/fetch run as optional profiles. Minimum RAM is about 16 GB on the host (8 GB Docker VM); eval mode targets an 8 GB laptop.

### Cited Findings
- **Installer:** `curl -fsSL https://get.pipeshub.com/install | bash` checks Docker, RAM and disk, asks slim vs full, lets you customise the graph DB, broker and KV store, generates secrets and `.env`, and waits for health. Flags are `-y`, `--version`, `--reconfigure`, `--print-env-only` and `--build` — [README.md](GH/README.md); [install.sh](GH/install.sh)
- **Slim / Full / Eval matrix:**
  - Slim: image `:slim`, Neo4j, Redis Streams, 16 GB host / 8 GB Docker VM
  - Full: image `:latest` with the embedding model bundled (~1.3 GB), Neo4j, Kafka + ZooKeeper, 16 GB+, "Production, air-gapped"
  - Eval: slim without the sandbox and the Slack bot, 8 GB-class laptop, "MCP search/ask demos"

  Source: [ADVANCED_DEPLOYMENT.md](GH/deployment/docker-compose/ADVANCED_DEPLOYMENT.md) lines 58-74
- **Compose services:** pipeshub-ai (all-in-one), docker-socket-proxy and sandbox-image (profile `sandbox`), mongodb, redis, qdrant, web-readers-gateway, searxng, crawl4ai, arango (`graph-arango`), neo4j (`graph-neo4j`), etcd, zookeeper and kafka-1 — [docker-compose.yml](GH/deployment/docker-compose/docker-compose.yml)
- **Pluggable storage:**
  - Graph `DATA_STORE` (neo4j / arangodb)
  - Vector `VECTOR_DB_TYPE` (qdrant / opensearch / redis)
  - KV `KV_STORE_TYPE` (redis / etcd / postgres)
  - Broker `MESSAGE_BROKER` (kafka / redis streams / postgres queue tables)
  - Blob storage (local / S3 / Azure Blob), with S3 instance-IAM-role support
  - Redis Cluster and AWS MemoryDB (0.8.0)

  Sources: [AGENTS.md](GH/AGENTS.md); CHANGELOG.md; [docs/connectors/s3-iam-role.md](GH/docs/connectors/s3-iam-role.md)
- **In-flight on main (2026-11-01):** a document-store migration tool from MongoDB to PostgreSQL (#5172, #5167, #5164) — git log
- **Ports:** Docker serves the UI, API and MCP together on 3000. Helm publishes the combined service on 3001 — AGENTS.md
- **Helm chart:** templates for arango, etcd, kafka, mongodb, neo4j, qdrant, redis and zookeeper StatefulSets, plus HPA, ingress, networkpolicy, pdb, externalsecret and an S3 SigV4 preload. Values files are `values-cloud.yaml`, `values-eks.yaml`, `values-eks-single-az.yaml` and `values-local.yaml`. The chart requires `qdrant.apiKey` (1.0.0) — [deployment/helm/pipeshub-ai/](GH/deployment/helm/pipeshub-ai)
- **Platforms:** Windows support (0.4.0) and a hot-reload dev compose — CHANGELOG.md
- **Cloud:** "PipesHub Cloud" at https://app.pipeshub.com is a "fully managed PipesHub … same connectors, permission-aware search, and APIs" — [README.md](GH/README.md)

### Gaps
- Cloud pricing, regions and SLA (site unreachable).

## Recent additions (release cadence)

### Takeaway
Releases came every 2-4 weeks between Oct 2025 (0.1.0-alpha) and Oct 2026 (1.0.0), and velocity is very high: 719 PRs went into 1.0.0 and 373 into 0.10.0. The 2026 themes were the new agent loop, MCP and PATs, code graph, permissions unification, staged indexing, Projects, service accounts and custom API actions.

### Cited Findings
- **Release history:**

  | Release | Date | Highlights |
  | --- | --- | --- |
  | 1.0.0 | 2026-10-27 | Code graph; custom API actions; Zendesk; focused reading and timeline search; `POST /api/v1/retrieve`; staged indexing; one access check; SAML/OIDC removed |
  | 0.10.0 | 2026-10-12 | Trash; KG entity tools; model discovery; prompt caching; Artifacts gallery (Beta); sandbox lockdown |
  | 0.9.0 | 2026-09-28 | Projects; service accounts; device login; SMB, CIFS, Drupal Wiki |
  | 0.8.0 | 2026-09-14 | Fair scheduling; Redis Cluster |
  | 0.7.0 | 2026-08-26 | PATs; GitHub Teams connector; Bedrock; multimodal embeddings; installer |
  | 0.6.0 | 2026-08-10 | New agent loop stable; ~1.8× query throughput |
  | 0.5.0 | 2026-07-01 | Atlassian DC; action tools; OpenRouter, LiteLLM, LM Studio |
  | 0.4.x | May 2026 | Next.js UI; sandbox and artifacts; image, STT and TTS models; web search; Helm; attachments; Vertex AI |
  | 0.3.0 | 2026-03-20 | Toolsets; MCP server; Slack bot; Neo4j |
  | 0.2.0 | 2026-02-11 | 15+ connectors; multi-instance; OAuth2 provider; Collections |
  | 0.1.0 | 2025-12-23 | First stable release |

  Sources: [CHANGELOG.md](GH/CHANGELOG.md); [changelog/](GH/changelog)
- **On main since 1.0.0:**
  - Connector setup extension points (#5139)
  - Verified document reads with per-writer tokens (#5118)
  - Classify-on-first-use (#5021)
  - Lane pools (#5020)
  - Sandbox secret masking (#5101)
  - Reranker removal (#5162)
  - Postgres document-store groundwork (#5164-#5172)
  - Many "sync and stream no longer create two records" connector fixes (Notion, Jira, GitHub/GitLab, Linear, Zammad)

  Source: `git log` (shallow, 2026-10-30 to 2026-11-01)

### Gaps
- GitHub release pages and full commit history: the clone is shallow and github.com release pages were not fetched. CHANGELOG.md is the registry of releases.
