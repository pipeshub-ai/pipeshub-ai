# Onyx (formerly Danswer): product feature inventory, as of November 2026

**How this was researched.** The session's network blocked `onyx.app` and `docs.onyx.app`. In v5.0.2, Onyx moved its documentation into the main repository (PR #16860, shown on the [GitHub releases page](https://github.com/onyx-dot-app/onyx/releases)). So the primary source here is the docs source in a shallow clone of `onyx-dot-app/onyx` at commit `3eb6420` (2026-11-01). The clone has 410 `.mdx` pages, `documentation/changelog.mdx` covering v2.0.0 (Oct 2025) to v5.0.0 (Oct 23, 2026), and the code itself.

**Citation convention.** Each docs citation links to the published URL `https://docs.onyx.app/<path>`, which matches the file path under `documentation/` in the repo. Code citations link to `github.com/onyx-dot-app/onyx/blob/main/...`. The changelog page is cited as [Changelog](https://docs.onyx.app/changelog).

**Current versions.** Latest is v5.0.3 (Oct 30, 2026). Maintenance lines v4.9.6 and v4.8.10 are still patched. Helm chart is onyx-0.9.30 ([GitHub releases](https://github.com/onyx-dot-app/onyx/releases)).

---

## Editions, licensing and pricing (CE vs Business vs Enterprise vs Cloud)

### Takeaway
Onyx is open core:
- MIT-licensed outside the `ee/` directories, with three tiers: Community (free), Business and Enterprise.
- Onyx Cloud includes every Business and Enterprise feature. The multi-tenant cloud sells the Business plan, and single-tenant cloud is Enterprise with a 100-seat minimum.
- Since v5.0, Enterprise features are enforced by the license alone.
- Community covers chat, search, agents, MCP/OpenAPI plugins, web search, deep research, the code interpreter, image generation and Projects.
- The paid tiers add:
  - permission sync
  - user groups and RBAC
  - analytics and query history
  - SCIM
  - hooks
  - white-labeling
  - usage limits
  - the LLM gateway
  - Search Mode
  - running more than one SSO provider at once.

### Cited Findings
- **License split.** Everything under `backend/ee/`, `web/src/app/ee/` and `web/src/ee/` uses the "Onyx Enterprise License". Everything else is MIT Expat. Copyright is held by DanswerAI, Inc. — [LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/LICENSE)
- **Community scope.** CE "covers all of the core features for RAG, AI Chat, Agents, and Actions" — [README](https://github.com/onyx-dot-app/onyx/blob/main/README.md)
- **Free core.** "Chat, Internal Search, Web Search, Deep Research, custom Agents, Code Interpreter, Image Generation, Projects, and Plugins" are free in CE — [Open Source Statement](https://docs.onyx.app/overview/miscellaneous/open_source_statement)
- **Business features** (the EE snippet's card titles):
  - multiple SSO providers
  - permission-sync connectors
  - user groups and group permissions
  - appearance and theming
  - usage and analytics
  - service accounts
  - LLM Gateway
  - encrypted secrets (AES via `ENCRYPTION_KEY_SECRET`)
  — [Enterprise Edition](https://docs.onyx.app/admins/getting_started/enterprise_edition) ([snippet source](https://github.com/onyx-dot-app/onyx/blob/main/documentation/snippets/enterprise-edition.mdx))
- **Enterprise features** (everything in Business, plus):
  - SCIM / group sync
  - custom roles and permissions
  - hook extensions
  - configurable usage limits
  - full white-labeling
  - custom analytics and log export
  - Enterprise SLAs and priority support
  - custom deployments and region-specific processing
  — [Enterprise Edition](https://docs.onyx.app/deployment/enterprise_edition)
- **Who the docs say needs a paid plan:** "Organizations with more than 100 users", group-scoped access or multiple SSO providers, strict compliance, centralized user management, and inherited permissions — [Enterprise Edition](https://docs.onyx.app/admins/getting_started/enterprise_edition)
- **Billing-page lists.**
  - Business: inherit document permissions, query history and usage dashboard, RBAC, encryption of secrets, service-account API keys, LLM gateway from external tools, optional self-hosting, custom theming.
  - Enterprise: SCIM, hook extensions, full white-labeling, custom roles, configurable usage limits, custom deployments, region-specific data processing, SLAs.
  - Trial: Cloud has a 1-month free trial with no credit card. New Business subscriptions also start with a 1-month trial.
  - Self-hosted paid plans get a locally validated license, and manual license upload is supported for air-gapped installs.
  — [Plans & Billing](https://docs.onyx.app/admins/billing/overview), [Subscribing](https://docs.onyx.app/admins/billing/subscribing)
- **v5.0 licensing change.** `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES` and `LICENSE_ENFORCEMENT_ENABLED` were removed. "Enterprise features follow the license alone." Unlicensed EE deployments now run as CE, and a lapsed self-hosted license can self-downgrade to CE — [Changelog v5.0.0](https://docs.onyx.app/changelog)
- **Fully open source, v2.1.0 (Oct 28, 2025).** The release was labelled "Fully Open Source (FOSS) Repository" — [Changelog](https://docs.onyx.app/changelog)
- **SSO in CE, v2.0.0 (Oct 2025).** "Single-sign on (SSO) added to Community Edition" — [Changelog](https://docs.onyx.app/changelog)
- **Cloud tenancy.** The multi-tenant cloud runs on AWS, defaults to US East, and offers the Business plan. Single-tenant cloud is Enterprise-only with a minimum of 100 licenses, in any region. It is either fully managed by Onyx on AWS or self-managed in the customer's cloud — [Multi-tenant](https://docs.onyx.app/security/onyx_cloud/multi_tenant), [Single-tenant](https://docs.onyx.app/security/onyx_cloud/single_tenant)
- **Price (secondary sources only; onyx.app/pricing was unreachable).** Aggregators report the Business / Cloud plan at about $20 per user per month billed annually, about $25 billed monthly, and Enterprise custom. An older July 2025 listing showed $16 per seat — [aitoolsatlas](https://aitoolsatlas.ai/tools/onyx/pricing), [xpay.sh](https://www.xpay.sh/saas-pricing/onyx-app/), [softwarefinder](https://softwarefinder.com/artificial-intelligence/onyx-ai)

### Inferences
- Onyx gates enterprise identity and governance behind paid tiers: groups, permission-inherited ACLs, analytics, SCIM, hooks and spend limits. Permission sync, the core "secure enterprise search" feature, is not free. CE effectively serves single-team or flat-permission deployments.
- The "more than 100 users" and 100-seat single-tenant minimum suggest Onyx's enterprise motion targets mid-market and larger customers.

### Gaps
- Official per-seat prices could not be verified because onyx.app was blocked. The figures above come from third-party aggregators.
- **Craft edition is contradictory in Onyx's own docs.**
  - [Craft overview](https://docs.onyx.app/overview/core_features/craft), [Craft Apps](https://docs.onyx.app/overview/core_features/craft_apps) and [Scheduled Tasks](https://docs.onyx.app/overview/core_features/craft_scheduled_tasks) say "available in all editions".
  - [Admin → Craft](https://docs.onyx.app/admins/managing_features/craft) and the [Open Source Statement](https://docs.onyx.app/overview/miscellaneous/open_source_statement) say "Craft needs a Business or Enterprise plan, or Onyx Cloud. On the Community Edition the Craft section shows an upgrade page."
  - Treat Craft as paid-tier-gated in practice. This is unresolved.

---

## Chat & search UX

### Takeaway
Onyx has a single input bar that switches between Chat mode and a Search mode for document lists; Search mode is Business+.

**Chat features:**
- citations with a sources panel
- per-chat filters for source, document set, tag and time
- file uploads
- Projects
- incognito chats
- memory
- multi-model side-by-side answers
- Deep Research
- per-chat reasoning and temperature controls
- durable runs that survive disconnects
- chat sharing
- feedback (thumbs up/down)
- prompt shortcuts

**Built-in actions:** Internal Search, Web Search, Open URL, Code Interpreter, Coding Agent, Image Generation and voice.

**Artifacts:** Rich artifacts and app building live in a separate product surface called **Craft**.

### Cited Findings
- **Input bar.**
  - Attach files by button, drag, or pasted image. Attached files appear as "User Files" in the sources sidebar.
  - The actions menu lets a user force or block each built-in action per chat: Internal Search (expandable to selected sources), Web Search, Code Interpreter, Coding Agent, Image Generation. MCP and OpenAPI plugins are toggled per chat under "Plugins".
  — [Chat UI](https://docs.onyx.app/overview/core_features/chat)
- **Deep Research.**
  - A toggle per message that runs "many cycles of thinking, research, and actions".
  - It can take several minutes and cost more than 10x the tokens of a normal answer.
  - Admins can disable it. It is not available in multi-model mode.
  - It became multilingual in v2.10 (Jan 2026).
  — [Chat UI](https://docs.onyx.app/overview/core_features/chat), [Changelog v2.10.0](https://docs.onyx.app/changelog)
- **Model selector.**
  - Optional **Auto** mode routes each message through a router model, using a gateway such as OpenRouter.
  - Per-model Reasoning Level and Temperature controls (admins can hide them).
  - **Add Model** for multi-model generation side by side.
  - Users set a default model, reasoning level and temperature under Settings → Chats.
  — [Chat UI](https://docs.onyx.app/overview/core_features/chat)
- **Multi-Model Chat (v3.2.0, Apr 2026).** Sends one prompt to several models in parallel and lets the user pick the response to continue with. It also works in the Chrome extension — [Changelog](https://docs.onyx.app/changelog)
- **Sources panel.** Shows "Cited Sources" in citation order and "Found Sources / More" for the other retrieved documents. Selecting a document adds its full content to the next message — [Chat UI](https://docs.onyx.app/overview/core_features/chat)
- **Projects.**
  - Group chats with shared Files and Instructions.
  - Existing chats can be moved into a Project.
  - When Project files exceed the context window, Onyx searches them (RAG over the project).
  - Launched in v2.0.0. In v2.4 the per-file token limit for Projects rose to 100k.
  — [Chat UI](https://docs.onyx.app/overview/core_features/chat), [Changelog](https://docs.onyx.app/changelog)
- **Incognito Chat (v4.6).**
  - Not saved to history and not used for memory.
  - Admins choose Off / Everyone / Designated Groups.
  - Records are "Usage Only" (the default) or "Full History".
  — [Chat UI](https://docs.onyx.app/overview/core_features/chat), [Security Hardening](https://docs.onyx.app/admins/advanced_configs/security_hardening)
- **Memory.** Settings → Memory has "Reference Stored Memories" and "Update Memories" toggles plus View/Add editing of the stored lines. The built-in tool lives at `tools/tool_implementations/memory` — [Chat UI](https://docs.onyx.app/overview/core_features/chat), [code](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/tools/tool_implementations)
- **Search mode.**
  - Returns source cards instead of an answer. Onyx expands the query and runs it against the index.
  - Filters for time range and source / document set.
  - Users can set "Default App Mode" to Search.
  - Admins can disable it. It requires Business+ or Cloud.
  — [RAG and Search](https://docs.onyx.app/overview/core_features/internal_search), [Chat Preferences](https://docs.onyx.app/admins/managing_features/chat)
- **Unified Search and Chat (v3.0.0, Mar 2026).** Both modes share one input bar with animated transitions — [Changelog](https://docs.onyx.app/changelog)
- **Per-chat filters (v4.9).** Sources, document sets, tags and time range are stored per chat rather than as one global preference — [Changelog v4.9.0](https://docs.onyx.app/changelog)
- **Auto-filters and time filtering.** "Auto-Detect Search Filters" infers source and time filters from the query, for example "in Slack" or "last quarter". Time-based search filtering shipped in v4.4 — [Chat Preferences](https://docs.onyx.app/admins/managing_features/chat), [Changelog v4.4.0](https://docs.onyx.app/changelog)
- **Durable Chat Runs (v4.2, Jun 2026).** Chat and Deep Research runs execute to completion on the server despite client disconnects. Reopening from any tab replays the missed stream — [Changelog](https://docs.onyx.app/changelog)
- **Chat History Compression (v2.12).** Older messages are summarized automatically in long chats — [Changelog](https://docs.onyx.app/changelog)
- **Chat sharing.**
  - A share button was added in v2.4.
  - Shared chats live at the `/app/shared/[chatId]` route and render full multi-model comparisons.
  - Anonymous access is available via "Allow Anonymous Users".
  — [Changelog v2.4.0 / v4.4](https://docs.onyx.app/changelog), [web routes](https://github.com/onyx-dot-app/onyx/tree/main/web/src/app), [Chat Preferences](https://docs.onyx.app/admins/managing_features/chat)
- **Feedback.** Thumbs up/down are shown in Analytics. The admin "Document Feedback" and "Documents Explorer" pages let admins boost or hide documents with a score editor and `BoostStatus` — [Analytics](https://docs.onyx.app/admins/analytics/overview), [administrative.py `/admin/doc-boosts`](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/server/manage/administrative.py), [web admin/documents/feedback](https://github.com/onyx-dot-app/onyx/tree/main/web/src/app/admin/documents)
- **Prompt shortcuts (prompt library).** Uniqueness constraints and usability enhancements were added in v2.11 (Feb 2026). The backend feature is `server/features/input_prompt` — [Changelog](https://docs.onyx.app/changelog), [code](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/server/features)
- **Image generation.**
  - Available in all editions.
  - Providers: OpenAI, Azure, Vertex (Nano Banana, added v2.10) and LiteLLM.
  - The default became `gpt-image-2.5-flare` in v5.0 after OpenAI retired `gpt-image-1`.
  — [Image Generation](https://docs.onyx.app/overview/core_features/image_generation), [providers dir](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/image_gen/providers), [Changelog](https://docs.onyx.app/changelog)
- **Code Interpreter.**
  - Available in all editions; runs Python in a sandbox.
  - numpy, pandas, scipy and matplotlib are pre-installed.
  - Chat and Project files are passed in, generated files are returned, and charts render inline.
  - Default-on in Docker and Helm since v3.0.0. The code-execution alpha was v2.5.
  — [Code Interpreter](https://docs.onyx.app/overview/core_features/code_interpreter), [Changelog](https://docs.onyx.app/changelog)
- **Coding Agent (v4.0, opt-in).**
  - A sandboxed agent with file-download and bash tools, used to investigate a GitHub repository.
  - Limits: public repos only, a snapshot without commit history, and a time limit.
  — [Coding Agent](https://docs.onyx.app/overview/core_features/coding_agent), [Changelog v4.0.0](https://docs.onyx.app/changelog)
- **Web Search.**
  - Available in all editions.
  - Search providers: Exa, Serper, Brave, Google PSE, SearXNG (self-hosted), Tavily, Firecrawl.
  - Content fetchers: the built-in Onyx Web Crawler, Firecrawl, Exa, Tavily.
  - Open URL reads pasted links.
  — [Web Search](https://docs.onyx.app/overview/core_features/web_search), [web_search clients](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/tools/tool_implementations/web_search/clients)
- **Voice Mode (v3.1, Apr 2026).** Speech-to-text and text-to-speech in all editions once an admin configures it. Providers are OpenAI (GA Realtime API since v4.1), Azure, ElevenLabs and Zoom — [Voice Mode](https://docs.onyx.app/overview/core_features/voice_mode), [voice providers](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/voice/providers)
- **Artifacts and Craft (v4.x).**
  - The README lists "Artifacts: Generate documents, graphics, and other downloadable artifacts" and "Secure Sandbox".
  - Craft is a separate "AI coworker" surface at `/craft`. It plans, writes and runs code in a sandbox, and produces decks, reports, spreadsheets, dashboards and web apps with live previews.
  - It uses company knowledge, a "Library" of reusable files, Skills, Craft Apps and MCP plugins.
  — [README](https://github.com/onyx-dot-app/onyx/blob/main/README.md), [Craft](https://docs.onyx.app/overview/core_features/craft)
- **Craft Skills.**
  - Built-ins: `pptx`, `company-search`, Browser, image-generation and craft-documentation, plus one per connected app.
  - Custom and personal skills can be shared with users, groups or everyone.
  - Skills can be ingested from GitHub (`skills/ingest_from_github.py`).
  — [Craft Skills](https://docs.onyx.app/overview/core_features/craft_skills), [skills/builtin](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/skills/builtin)
- **Craft Scheduled Tasks.**
  - Recurring Craft prompts on an interval, or daily/weekly at set times.
  - Each run starts a fresh session as the task's creator.
  - Per-plugin approval settings apply to unattended runs, and run history is kept.
  — [Scheduled Tasks](https://docs.onyx.app/overview/core_features/craft_scheduled_tasks)
- **Custom Chat Backgrounds and refreshed User Settings (v2.11).** UI personalization — [Changelog](https://docs.onyx.app/changelog)
- **UI languages.**
  - v4.7 added Spanish, Portuguese, French, German, Japanese, Simplified Chinese and Korean.
  - v4.8 added Arabic and a right-to-left interface.
  - v5.0 added a workspace default language.
  — [Changelog](https://docs.onyx.app/changelog)

### Inferences
- Onyx now positions itself as "the context layer for your team and AI agents" (README headline). Chat is one surface among several: Craft, MCP server, CLI and LLM gateway.
- No "canvas" co-editing document UI exists in main chat. Artifact creation runs through Code Interpreter file outputs and through Craft.

### Gaps
- No document mentions a user-facing "saved prompts library" beyond prompt shortcuts.
- The older "Agent Search" / agentic-search mode from early 2025 does not appear in current docs. It appears to have been superseded by Deep Research and the v2.2 agent framework revamp. No explicit deprecation note was found.

---

## Agents & actions

### Takeaway
Agents (formerly "Assistants" / "Personas") combine:
- instructions
- conversation starters
- knowledge scoping: connectors, document sets, specific folders and files, uploaded files, and a knowledge cutoff date
- built-in actions
- plugins: MCP, OpenAPI and Craft Apps
- a default model
- prompt reminders and the option to overwrite the system prompt
- granular sharing with users or groups at owner/editor-level permissions

Onyx is an MCP **client** (Streamable HTTP or SSE; OAuth with dynamic client registration, OAuth pass-through, or per-user and shared API keys). It is also an MCP **server**, on by default since v5.0 with OAuth sign-in for Claude Code, Codex, Claude Desktop and ChatGPT. Since v5.0, MCP, OpenAPI and Craft Apps are unified on one Plugins page.

### Cited Findings
- **Agent editor sections:**
  - Instructions
  - Conversation Starters
  - Knowledge: connectors, Document Sets, uploaded files
  - Actions
  - Advanced Options: reminders and overwriting the default system prompt
  - Share: featuring and labels for admins
  — [Custom Agents](https://docs.onyx.app/overview/core_features/agents)
- **Knowledge selection and admin controls.**
  - An agent with knowledge enabled but nothing selected searches everything the user can access.
  - Knowledge can be picked by Document Set, or by browsing a source type's folders and files (selecting a folder includes its subfolders).
  - Sharing an agent does not share its documents.
  - Each agent has a Default Model setting.
  - Knowledge Cutoff Date, Prompt Reminders and Overwrite System Prompts are available.
  - Admins can Feature (pins the agent to new users' sidebars), Unlist / Relist, choose the default agent, or require every chat to start in an agent.
  — [Admin Custom Agents](https://docs.onyx.app/admins/agents/overview), [Chat Preferences](https://docs.onyx.app/admins/managing_features/chat)
- **Who can create agents.** In CE, everyone can. On Business, Enterprise and Cloud, creation can be restricted via the "Create Agents" group permission. Per-agent usage statistics are a paid feature (route `/ee/agents/stats/[id]`) — [Admin Custom Agents](https://docs.onyx.app/admins/agents/overview), [web routes](https://github.com/onyx-dot-app/onyx/tree/main/web/src/app/ee)
- **Granular Agent Sharing (v4.2).** Explicit owners, ownership transfer, per-share permission levels, and guards that stop editors attaching knowledge they cannot access — [Changelog](https://docs.onyx.app/changelog)
- **Agent framework history.**
  - "Complete Agent Framework Revamp" in v2.2 (Nov 2025).
  - Revamped agent creation in v2.7.
  - A redesigned agent message timeline in v2.12.
  — [Changelog](https://docs.onyx.app/changelog)
- **Conversation-Aware Search Scoping (v4.3).** Internal search decides per call which sources to cover based on the agent's routing instructions; for example, "check Zendesk first, then Confluence" walks the sources in order — [Changelog](https://docs.onyx.app/changelog)
- **Built-in agent actions:** Internal Search, Web Search, Image Generation, Code Interpreter, Coding Agent, Open URL. Voice is also listed as needing admin setup — [Custom Agents](https://docs.onyx.app/overview/core_features/agents)
- **Built-in tools in code:** `search`, `web_search`, `open_url`, `python`, `images`, `bash`, `coding_agent`, `file_reader`, `memory`, `knowledge_graph`, `mcp`, `custom` (OpenAPI) — [tool_implementations](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/tools/tool_implementations)
- **Plugins page (v5.0).**
  - Plugin types: MCP (Chat, custom agents, Craft), OpenAPI (Chat, custom agents), Craft App (Craft only).
  - Admins control access (Organization or user groups; groups need Business+), "Enable in default Chat" and "Enable for Craft".
  - Users get personal defaults and per-user account connection.
  — [Plugins](https://docs.onyx.app/admins/plugins/overview), [Changelog v5.0.0](https://docs.onyx.app/changelog)
- **MCP client.**
  - Auth options:
    - No auth
    - Shared API key
    - Personal API key
    - OAuth: auto-setup via dynamic client registration, your own OAuth client, or a client metadata document
    - OAuth Pass-through (forwards the user's Onyx OIDC/OAuth token)
  - Custom headers with `{api_key}` / `{user_email}` placeholders.
  - Streamable HTTP is the default transport, with SSE as legacy.
  - Per-tool enable/disable and "Refresh tools".
  - Craft tool approval policies: Auto-approve / Ask / Disabled.
  - History: SSE MCP support in v2.0, MCP passthrough auth in v2.6, user MCP actions in v2.11, OAuth for known MCP providers in v4.1.
  — [MCP plugins](https://docs.onyx.app/admins/plugins/mcp), [Changelog](https://docs.onyx.app/changelog)
- **OpenAPI actions.**
  - Auth: none, custom auth header, per-user OAuth, OAuth pass-through.
  - Request placeholders: `CHAT_SESSION_ID`, `MESSAGE_ID`, `USER_ID`, `USER_EMAIL`.
  - Since v5.0 they pass an SSRF policy: no redirects, 10s connect / 120s read timeouts, loopback blocked, metadata IPs always blocked.
  — [OpenAPI plugins](https://docs.onyx.app/admins/plugins/openapi), [Changelog v5.0.0](https://docs.onyx.app/changelog)
- **Craft Apps.** Built-in catalogs for Slack, Google Calendar, Google Drive, Gmail, Linear, GitHub, HubSpot, Notion and Zendesk. They are read/write as the user with action-level approvals, and custom apps can be added — [Craft Apps](https://docs.onyx.app/overview/core_features/craft_apps), [external_apps/providers](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/external_apps/providers)
- **Onyx MCP server.**
  - Lets external clients search the documents a user can access, search the web, and fetch URLs.
  - OAuth approval flow; PATs and API keys still work.
  - Users need the `read:search` and `create:user_api_keys` permissions.
  - Available in all editions and on by default in Compose and Helm since v5.0.
  - Connections are reviewed and revoked under Settings → Accounts & Access → Connected apps.
  - The MCP server first shipped in v2.6 (Dec 2025).
  — [Onyx MCP server](https://docs.onyx.app/overview/onyx_anywhere/mcp_server), [Changelog](https://docs.onyx.app/changelog)
- **Workflows.** Craft Scheduled Tasks cover interval or daily/weekly automation, with per-plugin approval settings for unattended runs — [Scheduled Tasks](https://docs.onyx.app/overview/core_features/craft_scheduled_tasks)
- **Agent Wiki.** A separate docs tab, "agent-wiki", covers a wiki/editor product: Triggers, Auto-organize, Chats, MCP, Templates, Update Policies, and its own Docker Compose / Kubernetes deployment — [docs tree `documentation/agent-wiki/`](https://github.com/onyx-dot-app/onyx/tree/main/documentation/agent-wiki)

### Inferences
- Onyx's agent model is "knowledge-scoped persona plus tools", while multi-step autonomous work and approvals are concentrated in Craft.
- There is no general visual workflow or DAG builder; scheduled Craft prompts are the automation primitive.

### Gaps
- I did not read the Agent Wiki pages in detail. It appears to be a newer, separately deployed product, and its edition and GA status are unverified.
- No documented per-agent token budget was found beyond group and user spending limits.

---

## Connectors

### Takeaway
Onyx's `DocumentSource` registry holds about 58 production connectors. The README says "50+", and there are about 61 official doc pages, counting Confluence Cloud / Data Center and Slack indexed / federated variants. Federated Slack queries Slack live, and an Ingestion API sits alongside the connectors. Unofficial community connectors appear with `SHOW_EXTRA_CONNECTORS=true`.

Connector mechanics:
- **Polling:** refresh every 30 minutes by default.
- **Pruning:** every 7 days by default.
- **Index-after date.**
- **Scheduled connection health checks** (new in v5.0).
- **Failure handling:** document-level failure lists with retry, and auto-pause on repeated failures.
- **Access control:** three document-access modes (Public, Specific Groups, Sync with the source) plus connector-management roles (Operator, Editor).

Document-level permission sync is Business+ and covers 16 connectors. As of v5.0 permission sync is stored in Postgres.

### Cited Findings
- **Connector list (code, `backend/onyx/connectors/registry.py`):**
  - Airtable, Asana, Axero, Azure Data Lake, Bitbucket, BookStack, Box, Braintrust, Canvas, ClickUp, Coda, Confluence, Discord, Discourse, Document360, Dropbox, Drupal Wiki, Egnyte
  - File, Fireflies, Freshdesk, GitBook, GitHub, GitLab, Gmail, Gong, Google Cloud Storage, Google Drive, Google Sites, Guru, Highspot, HubSpot, IMAP, Jira, Linear, Loopio, LumApps
  - MediaWiki, Notion, OCI Storage, OneDrive, Outline, Outlook, Productboard, R2, S3, Salesforce, SharePoint, Slab, Slack, Teams, TestRail
  - Web (crawler), Wikipedia, XenForo, YouTrack, Zendesk, Zoom, Zulip
  - plus a mock connector
  — [registry.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/registry.py), [connectors dir](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/connectors)
- **Docs-only sources.** The official docs also list Confluence Cloud and Confluence Data Center separately, S3 with three auth modes (access keys, assume-role, IAM role), and a Request Tracker page — [documentation/admins/connectors/official](https://github.com/onyx-dot-app/onyx/tree/main/documentation/admins/connectors/official)
- **Connector categories:** Knowledge Base & Wikis, Cloud Storage, Ticketing & Task Management, Messaging, Meetings, Sales, Code Repository, Other. Unofficial connectors are shown via `SHOW_EXTRA_CONNECTORS=true` — [Connectors overview](https://docs.onyx.app/admins/connectors/overview)
- **Document Access options:**
  - "Everyone in Your Organization" (public).
  - "Specific Groups" (private; groups need Business+).
  - "Sync with the source", optionally "Restrict Access to Groups", shown as "Auto-sync, restricted".
  - Changing access triggers a re-index.
  - **Connector Access** roles: Operator (schedule and monitor) and Editor (update or delete).
  — [Connectors overview](https://docs.onyx.app/admins/connectors/overview)
- **Permission-sync connectors (Business, Enterprise, Cloud):** Google Drive, Gmail, Confluence, Jira, Slack (indexed only), GitHub, Box, Canvas, SharePoint (certificate auth), OneDrive, Microsoft Teams, Outlook, Linear, HubSpot, YouTrack, Zoom.
  - Salesforce does field-level censoring rather than document permission sync.
  - Group membership is synced where the source supports it.
  - Permission changes propagate in minutes to an hour, and a manual "Sync Permissions" button exists.
  — [Connectors overview](https://docs.onyx.app/admins/connectors/overview), [ee/external_permissions](https://github.com/onyx-dot-app/onyx/tree/main/backend/ee/onyx/external_permissions)
- **Permission sync engine (v5.0).**
  - Moved from Redis to Postgres.
  - New `PERMISSION_SYNC_TIMEOUT_SECONDS` (default 6 hours) for group syncs. Document permission sync stops only after an hour without progress.
  - Source ACLs apply only on permission-synced connectors.
  - v4.0 added "Permission Sync Visibility" and v2.0 added permission sync status updates.
  — [Changelog](https://docs.onyx.app/changelog)
- **Schedule settings.**
  - Refresh Every: default 30 minutes; the default for new connectors is set in Index Settings.
  - Prune Every: default 7 days; deletes documents that no longer exist at the source.
  - Index Documents After: start date.
  - Run Checks Every: capability and health checks; default never.
  — [Connectors overview](https://docs.onyx.app/admins/connectors/overview)
- **Indexing Status page.**
  - Shows status, access type, document count and last indexed time.
  - Statuses: Indexed, Scheduled (with queue position), Indexing, Paused, Failed, Deleting.
  - "Manage Index" actions: Update Index, Full Re-index, Start Indexing (skips checks), Sync Permissions, Pause/Resume Indexing & Syncs, Delete.
  - Per-document failure list with retry of only the failed documents.
  — [Connectors overview](https://docs.onyx.app/admins/connectors/overview)
- **Failure handling and visibility.**
  - "Auto-Pause for Failing Connectors" (v2.5): too many errors pause the connector.
  - "End-to-End Indexing Visibility" (v4.0).
  - "Indexing Status, Rebuilt" (v5.0).
  - API endpoints exist for index attempts, indexing errors, currently failed status, prune, run-once and credential swap.
  — [Changelog](https://docs.onyx.app/changelog), [API reference files_connectors](https://github.com/onyx-dot-app/onyx/tree/main/documentation/developers/api_reference/files_connectors)
- **v5.0 connector UX.**
  - "Connector Checks": named checks on the create form.
  - "Accounts Shared Across Sources": credential families, for example one Google or Microsoft account for Drive, Gmail and Sites.
  - "Editing Connectors Through a Plan": plan-and-apply edits.
  - "Outlook, One Thread at a Time": thread-level docs, with a forced full re-index.
  - "Spreadsheets in Far Fewer Chunks".
  — [Changelog v5.0.0](https://docs.onyx.app/changelog), [connectors/credential_families.py, config_diff.py, planning_rule.py](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/connectors)
- **Federated Slack.**
  - Does not index Slack. Searches live at query time via the Slack Search API as each user's own authorized account and merges the results with indexed results.
  - Has configurable time window and message count, and can be added to Document Sets.
  - No index attempts, pruning or permission sync.
  - Available in all editions.
  - Advanced filtering was added in v2.4.
  — [Slack Federated](https://docs.onyx.app/admins/connectors/official/slack/slack_federated), [Changelog](https://docs.onyx.app/changelog)
- **Federated framework.** The `backend/onyx/federated_connectors/` framework currently contains only `slack` — [code](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/federated_connectors)
- **New or expanded connectors in 2026:**
  - OneDrive (v4.9).
  - Teams: meetings and transcripts, files and images (v4.9).
  - Outlook and Zoom (v4.8).
  - Multiple Google Drive and Gmail connectors (v4.4).
  - Box (v4.5).
  - Slack Enterprise Grid (v4.0).
  - Confluence OAuth (v2.2).
  — [Changelog](https://docs.onyx.app/changelog)
- **Ingestion API.** Programmatic document push with `doc_updated_at` / `doc_created_at` (these drive time filters and recency). A file upload API is also available — [Ingestion API guide](https://docs.onyx.app/developers/guides/index_files_ingestion_api)
- **Tabular file indexing (v3.3).**
  - XLSX and CSV are indexed as structured content: field-value chunks, per-sheet descriptors, and whole-file summary chunks.
  - Covers the upload, Google Drive, Blob and SharePoint paths.
  — [Changelog](https://docs.onyx.app/changelog)
- **Credentials.** Encrypted at rest and never shown again (AES with `ENCRYPTION_KEY_SECRET` is an EE feature) — [Connectors overview](https://docs.onyx.app/admins/connectors/overview), [Data Storage](https://docs.onyx.app/security/architecture/data_storage)

### Inferences
- Onyx's connector moat is breadth (about 58) plus permission sync on 16 major sources. The 2026 investment went into Microsoft 365 depth (OneDrive, Outlook, Teams meetings) and operability: checks, plan-based edits, the rebuilt status page and Postgres-backed permission sync.
- Federated (non-indexed) search exists only for Slack.

### Gaps
- The exact official connector count is not stated in the docs (the README says "50+"). The count of about 58 is mine, from the registry.
- Whether Request Tracker is still supported is unclear: it has a doc page but no registry entry.

---

## Retrieval quality

### Takeaway
**Onyx replaced Vespa with OpenSearch.** OpenSearch has been the only document index since v4.0.0 (May 2026), and v5.0 removed the last Vespa migration code.

Retrieval combines:
- hybrid vector and keyword search (`hybrid_alpha`, default 0.5)
- LLM query expansion with weighted reciprocal-rank fusion (RRF)
- an LLM or "decision model" selection step instead of a cross-encoder reranker (`RERANK_COUNT` is now ignored)
- contextual retrieval
- image captioning
- vector quantization
- tabular chunking
- recency from `doc_updated_at`
- admin document boosting

Multipass indexing is "temporarily unavailable". The knowledge-graph pipeline is effectively dormant.

### Cited Findings
- **OpenSearch-only index (v5.0).** "OpenSearch has been the only document index since v4.0.0; 5.0 deletes the leftover migration task…". `VESPA_*`, `MANAGED_VESPA`, `RERANK_COUNT` "and the knowledge-graph pipeline variables are now ignored" — [Changelog v5.0.0](https://docs.onyx.app/changelog)
- **Code confirms OpenSearch-only.** `get_default_document_index` builds only `OpenSearchDocumentIndex` or `OpenSearchIndexPair` (primary plus secondary index for re-index) — [document_index/factory.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/document_index/factory.py)
- **Hybrid search and query expansion.**
  - `hybrid_alpha` ranges from 0.0 (pure keyword) to 1.0 (pure vector); deployment default `HYBRID_ALPHA` = 0.5.
  - `run_query_expansion` has an LLM generate extra keyword queries, which run in parallel and are merged with weighted RRF; the original query counts double.
  - `num_docs_fed_to_llm_selection` adds LLM selection with reasoning returned.
  - Filters: source_type, document_set, created/updated date ranges, tags.
  — [Search API guide](https://docs.onyx.app/developers/guides/search_api_guide)
- **Search Decision Model.**
  - A dedicated decision model "ranks all candidate documents in one request and keeps the best ones", or a language model selects them.
  - It also decides how much surrounding text to add.
  - If it fails, search falls back to the chat model.
  - Configured at Admin → Decision Models.
  — [Chat Preferences](https://docs.onyx.app/admins/managing_features/chat), [RAG and Search admin](https://docs.onyx.app/admins/managing_features/rag_and_search)
- **Index Settings page (self-hosted only; not shown on Cloud or Lite).**
  - Embedding model: cloud models from Cohere, OpenAI, Google Vertex and Voyage, or self-hosted models with per-CPU throughput and memory shown.
  - BYO embedding via LiteLLM, Azure, Bifrost or a custom Hugging Face model.
  - Vector Quantization: None / 7-bit (4x less memory, OpenSearch 2.16+) / 1-bit (32x less, OpenSearch 3.6+) with full-precision rescoring.
  - Contextual Retrieval: LLM-written document summaries and per-chunk context.
  - Multipass Indexing is "temporarily unavailable".
  - Extract & Caption Images: a vision LLM captions images; images over 3 MB are resized.
  - Embedding rate limit.
  — [Index Settings](https://docs.onyx.app/admins/advanced_configs/search_configs)
- **Re-index options.** "Re-index All Connectors Then Switch" (the default), "Re-index Active Connectors Then Switch" and "Switch Before Re-index". The page shows progress counts, auto-retry and pause, "Re-index Attention" and "Cancel Re-index & Revert". Re-indexing reuses stored content and does not re-fetch from sources — [Index Settings](https://docs.onyx.app/admins/advanced_configs/search_configs)
- **Bundled stores.** OpenSearch 3.6.0 ships with Compose and Helm. External OpenSearch is supported, with resource-health warnings for disk, JVM and k-NN memory — [Index Settings](https://docs.onyx.app/admins/advanced_configs/search_configs), [opensearch README](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/document_index/opensearch/README.md)
- **Recency and boosting.** `doc_updated_at` drives time filters and recency. A `recency_bias` field appears in the search tool's OpenAPI schema example. Admin document boosts exist at `/admin/doc-boosts` — [Ingestion API guide](https://docs.onyx.app/developers/guides/index_files_ingestion_api), [OpenAPI plugins example](https://docs.onyx.app/admins/plugins/openapi), [administrative.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/server/manage/administrative.py)
- **Knowledge graph.** A `knowledge_graph` tool still exists in code but is "available only if KG is enabled and exposed" (`KG_ENABLED and KG_EXPOSED`). The v5.0 changelog says KG pipeline variables are ignored, and no user-facing KG docs exist — [knowledge_graph_tool.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/tools/tool_implementations/knowledge_graph/knowledge_graph_tool.py), [Changelog v5.0.0](https://docs.onyx.app/changelog)
- **Multilingual.** Deep Research became multilingual in v2.10. Embedding choice (for example Cohere or Google) handles multilingual retrieval; there is no specific doc on multilingual search — [Changelog](https://docs.onyx.app/changelog)
- **Embedding Model Configuration** was revamped in v2.6, and an embedding model "Language and Embedding Models" update shipped in v2.0 — [Changelog](https://docs.onyx.app/changelog)

### Inferences
- The retrieval stack has shifted from a Vespa plus cross-encoder reranking design (the 2024–2025 Danswer/Onyx architecture) to OpenSearch hybrid plus LLM-based selection. Comparisons citing "Vespa" or a "reranker" for Onyx are outdated.
- Knowledge-graph features (an early-2025 initiative) appear deprecated or dormant as of v5.0.

### Gaps
- No documentation states the chunk size or overlap.
- Whether a cross-encoder reranker still runs anywhere is unconfirmed; `RERANK_COUNT` being ignored suggests it does not.
- The time-decay formula is not documented.

---

## LLM support, model controls and cost controls

### Takeaway
LLM support:
- **Providers:** OpenAI, Anthropic, Azure OpenAI, Bedrock, Vertex / Google AI.
- **Gateways:** OpenRouter, LiteLLM Proxy, Bifrost, Portkey, Vercel AI Gateway, Nebius Token Factory.
- **Self-hosted:** Ollama, LM Studio, plus a generic OpenAI-compatible provider (vLLM and others).
- **Model management:** per-agent default model, model auto-update, visibility controls, Auto routing, prompt caching, cost tracking.

Cost controls:
- Group-restricted provider access: Business+.
- Spending limits by token or dollar: workspace scope is Business, per-user and per-group scope is Enterprise.
- Onyx can act as an OpenAI- and Anthropic-compatible **LLM gateway** for external tools (Business+).

### Cited Findings
- **Provider guides:** Anthropic, Azure OpenAI, Bedrock, Bifrost, custom inference provider, Google AI, LiteLLM Proxy, LM Studio, Nebius Token Factory, Ollama, OpenAI, OpenRouter, Portkey, Vercel AI Gateway — [AI models docs dir](https://github.com/onyx-dot-app/onyx/tree/main/documentation/admins/ai_models)
- **Provider constants in code** add `openai_compatible` and `vertex_ai` (Workload Identity is supported) — [well_known_providers/constants.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/llm/well_known_providers/constants.py)
- **Model management.**
  - Default Model; hidden vs visible models; Refresh models.
  - Auto Update for OpenAI, Anthropic and Vertex.
  - Vision marker.
  - Per-model rename and default/maximum reasoning level.
  - Reasoning Control for users.
  — [Language Models](https://docs.onyx.app/admins/ai_models/overview)
- **Per-chat reasoning and temperature** (v4.5). "Reasoning Effort That Reaches the Model" (v4.9) — [Changelog](https://docs.onyx.app/changelog)
- **Provider history.**
  - Generic OpenAI-compatible provider: v3.2.
  - LiteLLM and Bifrost: v3.1.
  - Ollama and OpenRouter: v2.0.
  - Tavily and Nebius: v4.3.
  — [Changelog](https://docs.onyx.app/changelog)
- **LLM Access Controls.** Group restrictions on providers need Business+. Agent restrictions (agent whitelists) are on every plan — [LLM Access Controls](https://docs.onyx.app/admins/advanced_configs/llm_access_controls)
- **Spending limits.** Token or cost caps; workspace scope is Business, per-user and per-group scope is Enterprise. The Usage page shows per-user spend with breakdown by model and flow, and a reset-aware limit banner appears in chat — [Spending Limits](https://docs.onyx.app/admins/advanced_configs/spending_limits), [Changelog v4.6.0](https://docs.onyx.app/changelog)
- **Group token rate limits.** Enterprise — [Users and Groups](https://docs.onyx.app/admins/user_management/users_and_groups)
- **Cost and usage observability.**
  - Prompt caching cost savings and cost tracking: v2.8 (Jan 2026).
  - LLM usage observability: v2.2.
  - "System LLM Usage" (v4.8) meters background usage such as contextual retrieval and KG extraction separately.
  - A `cost_overrides` / price table exists in code.
  — [Changelog](https://docs.onyx.app/changelog), [llm dir](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/llm)
- **LLM Gateway.**
  - Endpoints at `/gateway/v1`: OpenAI Chat Completions, OpenAI Responses (for Codex CLI), Anthropic Messages plus token count (Claude Code via `ANTHROPIC_BASE_URL`), and `GET /models`.
  - Requires a PAT with the "LLM Gateway — Use" scope.
  - Business+ or Cloud (the v4.6 changelog called it "Enterprise tier").
  — [LLM Gateway guide](https://docs.onyx.app/developers/guides/llm_gateway), [Changelog v4.6.0](https://docs.onyx.app/changelog)
- **Lite and air-gapped modes.**
  - "Onyx Lite" (v3.0) is a chat-only mode under 1 GB of memory with no indexing.
  - Air-gapped support arrived in v2.3, and `ONYX_AIRGAPPED=true` (v5.0) blocks Onyx-controlled outbound calls.
  — [README](https://github.com/onyx-dot-app/onyx/blob/main/README.md), [Changelog](https://docs.onyx.app/changelog)

### Inferences
- Onyx treats model governance (visibility, group access, spend caps, gateway) as a paid differentiator, while the provider breadth itself is free.

### Gaps
- Exact Cloud usage limits for trial and free tiers ("New Cloud Usage Limits", v2.7) were not quantified in the docs I read.

---

## Integrations & surfaces

### Takeaway
Surfaces:
- web app
- desktop app (Windows, macOS, Linux)
- Chrome extension (new tab, side panel, selection, omnibox)
- Slack bot (OnyxBot)
- Discord bot
- embeddable website widget
- MCP server
- Onyx CLI (TUI plus scripting)
- REST and public Search APIs
- LLM gateway
- Terraform provider
- a React Native mobile app, apparently early stage

**No Microsoft Teams bot was found.**

### Cited Findings
- **README surfaces:** web and desktop app, Slack and Discord bots, MCP server, Chrome extension, embeddable widget — [README](https://github.com/onyx-dot-app/onyx/blob/main/README.md)
- **Slack bot (OnyxBot).**
  - Supports multiple Slack apps, each mapped to agents.
  - Per-channel config: Agent, Document Sets, respond to tags only, only respond if citations are found, ephemeral replies, specific users or groups, follow-up tags.
  - Standard Answer categories are Business+.
  - Works in threads, channels and DMs, with "Continue in Web UI".
  - Uses Socket Mode, so no public webhook is needed.
  - Available in all editions.
  — [Slack](https://docs.onyx.app/overview/onyx_anywhere/slack), [Slack bot setup](https://docs.onyx.app/admins/getting_started/slack_bot_setup)
- **Discord bot (v2.10, Jan 2026).**
  - Per-server and per-channel agent, mention-only or every message, thread-only mode, works in forums.
  - Linked with `!register` and `!sync-channels`.
  - Answers as a shared service identity, so Discord users' permissions are not applied.
  — [Discord bot](https://docs.onyx.app/overview/onyx_anywhere/discord_bot), [Changelog](https://docs.onyx.app/changelog)
- **Chrome extension (v2.9, Jan 2026).**
  - Onyx on every new tab, a side panel, search or ask about selected text, ask about the current page, address-bar search.
  - Works on Cloud and self-hosted in all editions.
  — [Chrome extension](https://docs.onyx.app/overview/onyx_anywhere/chrome_extension), [Changelog](https://docs.onyx.app/changelog)
- **Desktop app** (preview in v2.6, updated v2.11).
  - Global shortcut, menu bar / tray launch, multiple windows.
  - Packages: .exe, .dmg, .deb, .msi and ARM64.
  - All editions.
  — [Desktop app](https://docs.onyx.app/overview/onyx_anywhere/desktop_app), [Changelog](https://docs.onyx.app/changelog)
- **Website widget (v2.9).**
  - A web component of about 100–150 KB gzipped with Shadow DOM isolation.
  - Floating launcher or inline display; SSE streaming; session persistence; optional citations.
  - Auth via a shared API key or each visitor's identity token.
  — [Website widget](https://docs.onyx.app/overview/onyx_anywhere/website_widget), [Changelog](https://docs.onyx.app/changelog)
- **Onyx CLI (v3.0; Go).**
  - Interactive chat TUI plus non-interactive commands (`onyx-cli search`, `agentic search`, `/model` switcher).
  - Installed via `uv tool install onyx-cli` or binaries.
  - All editions.
  — [CLI](https://docs.onyx.app/overview/onyx_anywhere/cli), [Changelog](https://docs.onyx.app/changelog)
- **Public Search API (v4.0).** `POST /api/search`. "Document Search APIs in Community Edition" (v2.5). Paginated document search API (v2.4) — [Changelog](https://docs.onyx.app/changelog), [Search API guide](https://docs.onyx.app/developers/guides/search_api_guide)
- **API reference groups:** actions, agents, analytics, chat, files/connectors, ingestion, misc, projects, query_history, search, usage, user_groups, user_management. A built-in OpenAPI explorer is off by default on self-hosted — [API reference dir](https://github.com/onyx-dot-app/onyx/tree/main/documentation/developers/api_reference), [Developers overview](https://docs.onyx.app/developers/overview)
- **Terraform provider.** Published on the Terraform Registry in v4.8, with `terraform-provider-onyx` in the repo — [Terraform provider docs](https://docs.onyx.app/admins/advanced_configs/terraform_provider), [Changelog v4.8.0](https://docs.onyx.app/changelog)
- **Mobile.**
  - `mobile/` holds an Expo / React Native app that "runs as a development build".
  - Changelog mentions: a model picker and Deep Research in the actions sheet (v4.8), Expo SDK 57 (v4.7).
  - No App Store or Play Store listing is referenced in the docs.
  — [mobile/README.md](https://github.com/onyx-dot-app/onyx/blob/main/mobile/README.md), [Changelog](https://docs.onyx.app/changelog)
- **Salesforce integration.** `integrations/salesforce` exists in the repo — [repo tree](https://github.com/onyx-dot-app/onyx/tree/main/integrations)

### Inferences
- Onyx is aggressively pursuing "Onyx inside your AI tools", meaning the MCP server, LLM gateway and CLI for Claude Code and Codex, in addition to chat surfaces.
- No Microsoft Teams bot is a notable gap given the M365 connector depth.

### Gaps
- No Teams bot appears in the code (`onyxbot/` covers Slack and Discord) or the docs. I cannot rule out it being on a roadmap.
- Mobile app GA or store availability is unverified.

---

## Admin & enterprise (SSO, SCIM, RBAC, analytics, audit, branding, multi-tenancy, compliance)

### Takeaway
**Identity:**
- **SSO:** OIDC, SAML (self-hosted only), Google OAuth and basic auth. The JWT backend can be stateless.
- **Multi-provider SSO:** one provider is free; several at once need Business+. SSO moved from env vars to the admin panel in v4.4.
- **SCIM 2.0:** Enterprise, since v3.0.
- **Group-based RBAC** since v4.7: Admin and Basic groups are built in, with custom groups, group permissions and Group Managers on Business+. "Curator" is a legacy concept.

**Governance:**
- **Paid-tier analytics:** Usage, Analytics and Query History with CSV export and anonymization.
- **Audit log stream:** SIEM-ready (v4.3).
- **Hooks:** Enterprise and self-hosted only.
- **White-labeling:** Business theming; full white-labeling on Enterprise.
- **Retention:** chat retention is Enterprise or Cloud.

**Security:**
- **Credentials:** service accounts and scoped PATs; AES secret encryption.
- **Hardening:** TLS across the data plane; SSRF controls.
- **Compliance:** SOC 2 Type II and GDPR.

### Cited Findings
- **SSO providers.**
  - CE gets one enabled provider of any type; Business and Enterprise get any number; Onyx Cloud supports Google and OIDC with required, verified allowed domains.
  - Account matching and relinking options exist.
  - SAML is self-hosted only, in all editions, and not on Cloud.
  — [SSO providers](https://docs.onyx.app/deployment/authentication/sso_providers), [SAML](https://docs.onyx.app/deployment/authentication/saml), [OIDC](https://docs.onyx.app/deployment/authentication/oidc)
- **Admin-panel SSO.** v4.4 removed `AUTH_TYPE`; SSO moved to the admin panel with Multi-Provider SSO. "SSO-Only Login" shipped in v4.5 — [Changelog](https://docs.onyx.app/changelog)
- **SCIM 2.0.**
  - User provisioning, updates, deprovisioning, and group sync at `/scim/v2`.
  - Bearer token auth, tested with Okta and Entra ID.
  - Requires a license (the SCIM page needs one since v5.0).
  — [SCIM](https://docs.onyx.app/deployment/authentication/scim), [Changelog v3.0.0 / v5.0.0](https://docs.onyx.app/changelog)
- **Users.**
  - Statuses: Active, Inactive, Invite Pending, Request to Join.
  - Account types: Standard, Service Account, Slack Bot, External User (a placeholder from permission sync), Anonymous.
  - Sign-up controls: Restrict Open Sign-Up, Restrict Email Domains, Disable Password Login.
  — [Users and Groups](https://docs.onyx.app/admins/user_management/users_and_groups)
- **Groups and RBAC.**
  - Custom groups need Business+; CE has only Admin and Basic.
  - Group permissions: Manage LLMs, Manage Connectors & Document Sets, Manage Actions, Manage Groups, Manage Service Accounts, Manage Slack/Discord Bots, Create Agents, Manage Agents, View Agent Analytics, View Query History, Create User Access Token.
  - Group Managers get scoped administration of one group.
  - "Custom Roles & Permissions" are listed as Enterprise.
  — [Understanding Permissions](https://docs.onyx.app/admins/permissions/understanding_permissions), [Enterprise Edition](https://docs.onyx.app/admins/getting_started/enterprise_edition)
- **Permission model history.** "Fully Group-Based Permissions" (v4.7) made admin equal to Admin-group membership. The "Curators can now create actions" note (v2.0) reflects the older curator model; a `users_and_groups_legacy` doc still exists — [Changelog](https://docs.onyx.app/changelog), [legacy doc](https://github.com/onyx-dot-app/onyx/blob/main/documentation/admins/user_management/users_and_groups_legacy.mdx)
- **API keys and tokens.**
  - Service-account API keys need Business+ or Cloud.
  - PATs since v2.3, with scoped PATs since v4.1. PATs need a paid plan on Cloud.
  - Password resets revoke PATs (v5.0).
  — [API Keys](https://docs.onyx.app/admins/user_management/api_keys), [Changelog](https://docs.onyx.app/changelog)
- **Analytics (Business, Enterprise, Cloud).**
  - Usage: active users, spend, tokens, top spender, per-user and system usage.
  - Analytics: queries and unique users per day, feedback trends, Slack channel stats, per-agent analytics.
  - Query History: filter, inspect, CSV export; visibility Show with User Info / Anonymized / Hidden.
  - Custom Analytics (Enterprise self-hosted) loads PostHog or GA scripts.
  — [Analytics](https://docs.onyx.app/admins/analytics/overview), [Query History](https://docs.onyx.app/admins/analytics/query_history), [Custom Analytics](https://docs.onyx.app/admins/analytics/custom_analytics)
- **Audit logging (v4.3).**
  - A normalized, SIEM-exportable event stream for Splunk, Sentinel, Elastic, Chronicle and AWS Security Lake.
  - Covers authentication, user and access management, groups, configuration and resources, and credential access.
  - Maps to SOC 2 CC7 and NIST 800-53 AU. Secrets are never logged.
  - Enterprise self-hosted admins can download raw logs (Log Export).
  — [Audit Logs](https://docs.onyx.app/admins/auditing/audit_logs), [Changelog](https://docs.onyx.app/changelog)
- **Hook Extensions (Enterprise, self-hosted only, not Cloud).**
  - Inject custom logic at hook points: Query Processing, Document Ingestion, Document Push.
  - Status, logs and health shown in the admin UI; Lambda example provided.
  - The README pitches it as "Run custom code to remove PII, reject sensitive queries".
  — [Hook Extensions](https://docs.onyx.app/admins/advanced_configs/hook_extensions), [hooks/points](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx/hooks/points), [README](https://github.com/onyx-dot-app/onyx/blob/main/README.md)
- **White-labeling.**
  - Business: logo, display name, logo style, greeting, chat header and footer text, login subtitle.
  - Also: System Announcement banner and First Visit Notice with required consent.
  - Enterprise: custom help link and full replacement of Onyx branding.
  — [White Labeling](https://docs.onyx.app/admins/advanced_configs/white_labeling)
- **Chat retention.** Forever, a preset period, or custom. Needs Enterprise or Cloud. "Flexible Chat Retention" shipped in v4.2 — [Chat Preferences](https://docs.onyx.app/admins/managing_features/chat), [Changelog](https://docs.onyx.app/changelog)
- **Security & Hardening admin page (v4.2+).**
  - Session expiry synced with the IdP.
  - Password policy and external JWT auth.
  - User-directory visibility.
  - Group restrictions on permission-synced connectors.
  - Incognito controls.
  - SSRF protection levels.
  — [Security Hardening](https://docs.onyx.app/admins/advanced_configs/security_hardening), [Changelog v4.1.1](https://docs.onyx.app/changelog)
- **Encryption.**
  - Onyx Cloud: AES-256 at rest and TLS 1.3 in transit.
  - Self-hosted relies on disk encryption, with AES credential encryption (EE) via `ENCRYPTION_KEY_SECRET`.
  - "TLS Across the Data Plane" shipped in v4.3.
  — [Data Storage](https://docs.onyx.app/security/architecture/data_storage), [Changelog](https://docs.onyx.app/changelog)
- **Compliance.** "Onyx is SOC 2 Type II and GDPR compliant", covering both multi-tenant and single-tenant Cloud. Region-specific processing is Enterprise — [Compliance](https://docs.onyx.app/security/onyx_cloud/compliance), [Enterprise Edition](https://docs.onyx.app/admins/getting_started/enterprise_edition)
- **Multi-tenancy.** The Cloud is multi-tenant on AWS US East. Subprocessors are OpenSearch, Grafana, Stripe, PostHog, Sentry, and OpenAI and Anthropic as default LLMs. The code includes `ee/onyx/server/tenants` and tenant usage limits — [Multi-tenant](https://docs.onyx.app/security/onyx_cloud/multi_tenant), [ee server dir](https://github.com/onyx-dot-app/onyx/tree/main/backend/ee/onyx/server)
- **Other EE server modules:**
  - analytics
  - billing
  - documents
  - enterprise_settings
  - evals
  - features (hooks)
  - gateway
  - license
  - log_export
  - manage
  - metrics
  - oauth
  - query_and_chat
  - query_history
  - reporting
  - scim
  - tenants
  - token_rate_limits
  - usage_limits
  - user_group
  - standard answers (route `/ee/admin/standard-answer`)
  — [backend/ee/onyx/server](https://github.com/onyx-dot-app/onyx/tree/main/backend/ee/onyx/server), [web ee routes](https://github.com/onyx-dot-app/onyx/tree/main/web/src/app/ee)
- **Deployment.**
  - Docker Compose, Kubernetes / Helm (KEDA autoscaling since v2.0), Terraform, plus guides for AWS EC2/EKS/RDS, Azure, GCP and DigitalOcean.
  - One-line install script.
  - High-availability and monitoring guides.
  - SeaweedFS replaced MinIO in v5.0.
  - External S3, GCS and native Azure Blob (v4.5) are supported.
  — [Deployment docs dir](https://github.com/onyx-dot-app/onyx/tree/main/documentation/deployment), [Changelog](https://docs.onyx.app/changelog)
- **Tracing and evals.** A tracing admin page (`/admin/tracing`) and an EE evals API exist — [Tracing doc](https://docs.onyx.app/admins/advanced_configs/tracing), [ee/server/evals](https://github.com/onyx-dot-app/onyx/tree/main/backend/ee/onyx/server/evals)

### Inferences
- In 2026 Onyx closed most enterprise checklist items (audit stream, SCIM, multi-SSO, group RBAC, spend limits, retention, TLS, runtime security settings), largely behind the license.

### Gaps
- No HIPAA or ISO 27001 claim was found in the docs; only SOC 2 Type II and GDPR are stated.
- No data-residency region list beyond "any region" for single-tenant.

---

## Notable launches, 2025–2026 (timeline from the changelog)

### Takeaway
Onyx shipped about 24 minor or major releases between Oct 2025 (v2.0) and Oct 2026 (v5.0), roughly every two to three weeks. The arc ran from:
1. a UI refresh, Projects and the agent revamp (late 2025)
2. Chrome extension, widget, Discord bot, MCP server and desktop (winter 2025–26)
3. unified search and chat, SCIM, CLI, Lite, Voice, Hooks and multi-model (Q1–Q2 2026)
4. the OpenSearch-only index, Coding Agent and public Search API (v4.0, May 2026)
5. durable runs, audit logs, multi-SSO, LLM gateway, i18n, group RBAC, Microsoft 365 connectors and Craft (Q3 2026)
6. the Plugins page and OAuth MCP (v5.0, Oct 2026)

### Cited Findings (all from [Changelog](https://docs.onyx.app/changelog))
- **v2.0.0 (Oct 20, 2025):** UI refresh, SSO in CE, Projects, new Compose file, curators can create actions, search filters, SSE MCP servers, Ollama and OpenRouter, one-line deploy script, KEDA autoscaling.
- **v2.1–v2.6 (Oct–Dec 2025):** OAuth actions, FOSS repository, agent framework revamp, new login and onboarding flow, PATs, air-gapped support, LLM access controls, share chat button, paginated document search API, code execution alpha, document search APIs in CE, auto-pause for failing connectors, MCP server, desktop app preview, SearXNG, MCP passthrough auth, embedding model config.
- **v2.7–v2.12 (Jan–Mar 2026):** agent creation revamp, image generation config, new LLM config forms, prompt caching and cost tracking, Azure image generation, Chrome extension, widget, in-app notifications, Discord bot, Nano Banana, multilingual Deep Research, user MCP actions, prompt shortcuts, Brave search, chat history compression, chat and project search.
- **v3.0.0 (Mar 9, 2026):** SCIM 2.0, unified search and chat, Code Interpreter by default, Onyx CLI, Onyx Lite.
- **v3.1–v3.3 (Apr–May 2026):** Voice Mode, Hooks, redesigned Users and Groups, LiteLLM and Bifrost, multi-model chat, generic OpenAI-compatible provider, hook logs and document ingestion, tabular file indexing.
- **v4.0.0 (May 26, 2026):** Coding Agent (opt-in), end-to-end indexing visibility, permission sync visibility, Slack Enterprise Grid, public Search API. OpenSearch became the only document index.
- **v4.1–v4.5 (Jun–Aug 2026):** scoped PATs, OAuth for known MCP providers, voice on the GA Realtime API, required `USER_AUTH_SECRET`, durable chat runs, granular agent sharing, runtime security settings, flexible chat retention, audit logging, TLS across the data plane, conversation-aware search scoping, Tavily and Nebius, `AUTH_TYPE` removed with SSO in the admin panel, multi-provider SSO, time-based search filtering, system announcements, multiple Drive and Gmail connectors, SSO-only login, per-chat reasoning and temperature, Box connector, native Azure Blob store.
- **v4.6–v4.9 (Aug–Oct 2026):** LLM gateway for external tools, usage and cost management, incognito chat, UI in seven languages, fully group-based permissions, Outlook and Zoom connectors, Terraform Registry, Arabic and RTL, system LLM usage, OneDrive, Teams meetings and files, Onyx on Google Cloud, per-chat search filters.
- **v5.0.0 (Oct 23, 2026):** unified Plugins page, Onyx MCP with OAuth for AI clients (on by default), connector checks, shared accounts across sources, plan-based connector editing, rebuilt Indexing Status, connector access roles (Operator / Editor), Outlook thread docs, spreadsheet chunk reduction, SeaweedFS replacing MinIO, license-only EE gating, permission sync moved to Postgres, SSRF policy for OpenAPI tools, fleet telemetry.
- **Craft.** First appeared through 2026, with Helm charts named "Onyx application and Craft sandbox deployment" and Craft deployment docs (Docker Compose and Kubernetes) — [GitHub releases](https://github.com/onyx-dot-app/onyx/releases), [Deploying Craft](https://docs.onyx.app/deployment/local/craft)

### Inferences
- Onyx's 2026 strategy is to be the permission-aware context layer that external agents plug into: MCP server with OAuth, LLM gateway, CLI, Search API. Craft is the in-product agentic workspace. This positions Onyx against Glean and against "MCP-only, index-free" approaches, which the README explicitly argues against.

### Gaps
- Craft's exact launch version is not pinned; its changelog sections exist from about v4.x but I did not extract the first mention.
- No blog posts, Product Hunt or HN launch pages were consulted because onyx.app was network-blocked.
