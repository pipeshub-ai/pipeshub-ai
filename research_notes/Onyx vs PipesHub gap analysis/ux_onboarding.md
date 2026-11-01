# End-user and admin UX and onboarding: Onyx vs PipesHub (as of 1 Nov 2026)

Method and source notes (read first):
- **PipesHub** evidence comes from the local checkout at `/home/user/pipeshub-ai` (frontend at `frontend/`, CHANGELOG at 1.0.0, 27 Oct 2026). Paths below are relative to that repo.
- **Onyx** evidence comes from a shallow clone of `github.com/onyx-dot-app/onyx` at commit `3eb6420f` (1 Nov 2026), cloned to `/home/user/onyx-dot-app/onyx`. That repo holds the **source of docs.onyx.app** under `documentation/` (MDX pages and changelog through v5.0.0, 23 Oct 2026), so docs claims are cited as `onyx:documentation/...` paths, which map to `https://docs.onyx.app/<same path without .mdx>`. Web UI code is cited as `onyx:web/src/...`.
- **Network limits:** `docs.onyx.app` and `docs.pipeshub.com` did not resolve from this session (outside allowed domains). YouTube transcripts, onyx.app and pipeshub.com marketing pages were not read. The GitHub API for onyx-dot-app was not available, so Onyx GitHub issues were not reviewed. PipesHub's open issues were listed through the GitHub MCP.
- I did not run either product, so "screen" descriptions are rebuilt from code, i18n strings and docs, not from screenshots. Nothing here was timed by hand.

## First-run / onboarding (setup wizard, LLM config, first connector, sample data, empty states, tours, time to first answer)

### Takeaway
Onyx's first run is a short in-chat checklist: your name, then connect an LLM from 6 featured providers, then optional extras (web search, image generation, invite team). Embeddings work with no key, and both admins and non-admins get a version of the flow. PipesHub's first run is an admin-only, full-page system-configuration wizard (AI model, embedding model, storage, SMTP) with no personal step. Its guided tour is stubbed and never mounted. But PipesHub has something Onyx lacks: a bundled **Acme Corp demo dataset** with curated "golden questions", including a permission-restricted one, so a new user can get a cited answer before connecting any real source.

### Cited Findings
**Onyx**
- Install: the recommended path is `uv tool install onyx-cli && onyx-cli deploy install`, a guided installer. It checks resources (and installs Docker on Linux), offers **Lite** vs **Standard** modes, prompts for a version, then pulls the containers and waits for health. A one-line `curl … | bash` script and a PowerShell script wrap the same CLI — [onyx:documentation/snippets/quickstart.mdx](https://docs.onyx.app/overview/getting_started/quickstart)
- In-app onboarding is rendered inside the chat page (`OnboardingFlow` imported in `onyx:web/src/views/AppPage.tsx`, lines 86-89, 314-321). It has a PreStep ("Let's take a moment to get you set up."), then numbered steps: **Name** ("What should {appName} call you?"), **LLM setup** ("Connect your LLM models", "Connect a model to start chatting."), **Optional setup**, then **PostStep** ("You're all set."), with a "Step {current} of {total}" progress label and a progress circle — [onyx:web/src/lib/onboarding/constants.ts](https://github.com/onyx-dot-app/onyx/blob/main/web/src/lib/onboarding/constants.ts); strings in [onyx:web/src/i18n/messages/en.json](https://github.com/onyx-dot-app/onyx/blob/main/web/src/i18n/messages/en.json) (`onboarding.*`)
- The LLM step features 6 providers (OpenAI, Anthropic, Vertex AI, LiteLLM Proxy, Ollama, LM Studio). Every other provider is under "More Providers → Set up in Admin Panel" — [onyx:web/src/lib/onboarding/constants.ts](https://github.com/onyx-dot-app/onyx/blob/main/web/src/lib/onboarding/constants.ts) (`ONBOARDING_LLM_PROVIDERS`)
- The final step shows three cards that deep-link into admin: "Select Web Search Provider", "Enable Image Generation", "Invite Your Team / Manage Users" — same file, `FINAL_SETUP_CONFIG`
- Non-admins get a separate `NonAdminStep` (save name → "You're all set!") — [onyx:web/src/lib/onboarding/components/NonAdminStep.tsx](https://github.com/onyx-dot-app/onyx/tree/main/web/src/lib/onboarding/components)
- The onboarding flow was introduced in v2.2.0 (Nov 2025), "guides them through personalizing their workspace, setting up their LLMs, and configuring actions like Web Search and Image Generation" — [onyx:documentation/changelog.mdx](https://docs.onyx.app/changelog) (v2.2.0 "New Login UI and Onboarding Flow"). A later fix: "Fixed onboarding not scrolling at small viewports" (v4.3/4.4 range, same file, line ~1582)
- Embeddings run locally on the bundled model servers, so connectors, indexing and search need no external key; a provider key is needed only for chat — [Railway Onyx template](https://railway.com/deploy/onyx-ai) (third-party)
- The first user is automatically an admin — [onyx:documentation/admins/overview.mdx](https://docs.onyx.app/admins/overview)
- Admins can configure a **First Visit Notice** (optionally requiring consent) and a **System Announcement** pop-up. Both are white-label settings — [onyx:documentation/admins/advanced_configs/white_labeling.mdx](https://docs.onyx.app/admins/advanced_configs/white_labeling)
- I found no sample/demo dataset in the Onyx onboarding code or docs. The onboarding steps do not include connecting a source — [onyx:web/src/lib/onboarding/constants.ts](https://github.com/onyx-dot-app/onyx/blob/main/web/src/lib/onboarding/constants.ts)

**PipesHub**
- The onboarding wizard steps are `ai-model` (required, title "AI model*"), `embedding-model`, `storage`, `smtp`, then a hidden `loading` step. An `org-profile` step is commented out — `frontend/app/(main)/onboarding/types.ts`; step strings in `frontend/lib/i18n/locales/en-US.json` (`onboarding.steps.*`: "Connect an LLM to power search answers and insights", "Enable semantic search across your documents", "Choose where your indexed data is stored", "Set up email for invites and password resets")
- Only admins are redirected to `/onboarding`, and only while the org status is `notConfigured`. Members are never sent there — `frontend/app/(main)/onboarding/use-onboarding-redirect.ts`
- "Enter Pipeshub" is enabled only once the LLM step is saved — `frontend/app/(main)/onboarding/onboardingPage.tsx` (line ~186)
- The embedding step asks the admin to "Add an embedding provider… or click Skip to open Default (System Provided) and press Add model". That is a two-action workaround for using the default — `en-US.json` `onboarding.embeddingStepHint`, `onboarding.skipEmbeddingTitle`
- The wizard shows 14 LLM providers in its provider enum (openAI, anthropic, gemini, groq, mistral, cohere, xai, fireworks, together, bedrock, vertexAI, azureOpenAI, azureAI, ollama, openAICompatible) — `frontend/app/(main)/onboarding/types.ts` (`AiProvider`)
- The **guided tour is stubbed**. `getTourStatus` always returns step1, `markTourStepComplete` and `dismissTour` are `TODO` stubs against non-existent `/api/v1/tour/onboarding` endpoints — `frontend/app/(main)/components/tours/onboarding/api.ts` (lines 81-111). `TourCard` is not imported anywhere outside its own folder (grep of `frontend/app`)
- A **user-background survey** (role picker: Software Developer, Product Manager, Marketer, …) is mounted in the main layout — `frontend/app/(main)/root-layout.tsx:244`, `frontend/app/(main)/components/surveys/user-background/user-background-survey.tsx` (lines 19-30). Its role labels are hard-coded English, not i18n keys
- **Demo data:** a bundled "Acme Corp" Demo connector with sample accounts on the reserved domain `acme-demo.example`, plus a permission-restricted record ("Enterprise pricing strategy 2026") readable only by `bob@acme-demo.example` — `frontend/app/(main)/workspace/connectors/demo-data/demo-data.ts`
- While the demo is active, the new-chat landing shows 5 demo questions (e.g. "Why was the payment service architecture changed, and how was the decision made?") with source icons. The restricted question is shown locked to people who cannot see it, and admins get a hint to sign in as the reader. A "connect your own" link leads to Connectors — `frontend/app/(main)/chat/components/demo-suggestions.tsx`; `en-US.json` `chat.demoSuggestions`
- Demo sources carry a "Demo" badge in answers, search and record lists. A removal notice appears once real data is indexed ("Your company's own data is now in PipesHub…"), with "Turn off for everyone" / "Remove permanently" / "Keep for now" (7-day snooze) — `en-US.json` `demoData.*`; `demo-data.ts` (`REMOVAL_NOTICE_SNOOZE_MS`); shipped in 0.9.0 — `changelog/0.9.0.md` lines 67-68, 83, 108
- Without the demo, the landing shows 6 generic suggestion chips that reference connectors the user may not have (Slack latency feedback, Jira blockers, Notion vs Slack, …) — `en-US.json` `chat.defaultSuggestions`
- Open bug: "Self-hosted installation enables data collection by default without clear notice during setup" (#3625). Related: "Telemetry: no way to opt out before the first push" (#3299) — [PipesHub issues](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625), [#3299](https://github.com/pipeshub-ai/pipeshub-ai/issues/3299)

### Inferences
- **Onyx is ahead** on first-run friction for the first person in. It has a personal step, featured providers, a local embedding default that needs no decision, a non-admin path, and the wizard lives inside the chat page, so the user lands where they will work. PipesHub asks the admin to make four infrastructure decisions (embedding, storage, SMTP) before the first chat. Those choices have defaults and could be deferred.
- **PipesHub is ahead** on "time to first useful answer". The Acme demo plus golden questions (including a permission demonstration) gives a cited answer before any connector OAuth. Onyx's flow ends at "You're all set" with an empty index. Its final cards point to web search, image gen and users, not to connecting data.
- PipesHub's tour scaffolding exists but is dead code. Finishing it, or removing it, is a cheap gap to close. The hard-coded English survey labels break PipesHub's own i18n parity rule.

### Gaps
- I did not measure time-to-first-answer for either product on a real machine.
- I could not view Onyx's onboarding screenshot (`documentation/assets/changelog/v2.2/showcase_onboarding.webp`) or any YouTube walkthroughs.
- PipesHub's `docs.pipeshub.com` getting-started page could not be fetched, so the documented install-to-chat path is not covered here.

## Chat experience (layout, history, projects, agent picker, model switcher, citations, preview, follow-ups, shortcuts, search mode, uploads, regenerate/edit, sharing, feedback, dark mode, mobile)

### Takeaway
Both have mature chat UIs with near feature parity: projects, model picker, citations with a side panel, regenerate, feedback, sharing, dark mode and voice. Onyx leads on **multi-model side-by-side answers, incognito chat, memory, a distinct Search mode, per-chat search filters** and RTL. PipesHub leads on **block-level citation preview** in a resizable document viewer, **agent strategy modes**, in-chat tool approval cards, mermaid/table rendering, chat **fork**, follow-up "Ask more" suggestion sets, and an explicit **query-mode switcher** (internal search / web / image / agent).

### Cited Findings
**Onyx**
- Input bar: Attach Files (drag/paste), an Actions menu to force or block Internal Search / Web Search / Code Interpreter / Coding Agent / Image Generation, a Plugins toggle, and a **Deep Research** toggle (a note warns it can cost more than 10x the tokens) — [onyx:documentation/overview/core_features/chat.mdx](https://docs.onyx.app/overview/core_features/chat)
- Model selector: optional **Auto** router mode, Reasoning Level and Temperature controls, and **Add Model** for multi-model side-by-side generation (admin-enabled) — same page
- Sources panel: a **Sources** button under answers opens a right panel with "Cited Sources" in citation order and "Found Sources". Selecting a document injects its full content into the next message — same page
- **Projects** group chats with shared files and instructions, with "Move to Project". **Incognito Chat** keeps the chat out of history and memories. **Memory** is managed under Settings → Memory — same page; incognito details in [changelog v4.6](https://docs.onyx.app/changelog) ("Incognito Chat")
- A separate **Search mode** shows documents as source cards, with time and source filters. Users can set "Default App Mode" to Search — [onyx:documentation/overview/core_features/internal_search.mdx](https://docs.onyx.app/overview/core_features/internal_search)
- Search filters (sources, document sets, tags, time) are stored **per chat** since v4.9 — [changelog v4.9.0](https://docs.onyx.app/changelog) "Search Filters That Belong to the Chat"
- Multi-model: "narrow screens get a swipeable single-response carousel, panel headers stay pinned"; shared chats render the full multi-model comparison — [changelog v4.7.0 / v4.4](https://docs.onyx.app/changelog)
- A Share Chat button sits at the top right of the chat (added v2.3) — [changelog](https://docs.onyx.app/changelog) "Share Chat Button"
- Dark mode persists across browsers and reloads — [changelog](https://docs.onyx.app/changelog) (v2.1 and v4.3 entries)
- Mobile web: "chat is full-width on small screens, hover-revealed buttons are reachable on touch devices" (v4.5) — [changelog](https://docs.onyx.app/changelog)
- Per-user defaults for temperature and reasoning level are under Settings → Chat Preferences — [changelog v4.7.0](https://docs.onyx.app/changelog)

**PipesHub**
- Query modes: "Internal search", "Web search", "Image creation", "Agent". Answer modes: Internal / Web / Internal + web — `en-US.json` `chat.queryModes`, `chat.answerMode`
- Agent strategy picker: Auto / Quick / Plan & execute / Deep, each with a hint — `en-US.json` `chat.agentStrategy`; `frontend/app/(main)/chat/components/agent-strategy-dropdown.tsx`, `chat-panel/agent-strategy-mode-switcher.tsx`
- Message area components: citations (`response-tabs/citations`), confidence indicator, mermaid diagrams with zoom/copy/download, fullscreen tables with CSV download, artifacts panel, tool-approval card (Allow/Deny, "Don't ask again for this tool", expiry timer), ask-user-question card, MCP sign-in card, "jump to newest" — `frontend/app/(main)/chat/components/message-area/`; `en-US.json` `chat.toolApproval`
- Message actions: copy (as markdown / as text), regenerate, edit query, **fork / "Fork from here"**, like/dislike with categorised feedback (Incorrect info, Missing info, Poor citations, Well explained, …), read aloud (TTS), voice input — `en-US.json` `chat.*` keys (`regenerate`, `editQuery`, `fork`, `forkFromHere`, `feedbackCategory*`, `readAloud`, `voiceInput`)
- Follow-ups: "Ask more" question sets under answers — `frontend/app/(main)/chat/components/message-area/ask-more.tsx`; `en-US.json` `chat.askMore`, `chat.askMoreQuestionSets`
- Document preview: inline, sidebar, fullscreen and mobile variants, a citations panel synced to highlighted text, PDF zoom, version switcher, and sandboxed HTML — `frontend/app/components/file-preview/` (`citations-panel.tsx`, `use-citation-sync.ts`, `use-text-highlighter.ts`, `file-preview-mobile.tsx`, `version-switcher.tsx`)
- Sidebar: time-grouped chats, pinned, "Shared chats", "My agents", agent-scoped chat lists, project conversations, archive — `frontend/app/(main)/chat/sidebar/`; `en-US.json` `chat.pinned`, `chat.sharedChats`, `chat.archive`
- Keyboard shortcuts: Cmd/Ctrl+K opens the command palette, Cmd/Ctrl+Shift+K and Cmd/Ctrl+N start a new chat — `frontend/app/(main)/chat/sidebar/index.tsx` (lines 117-134); `chat/components/search/command-palette.tsx`
- @-mentions in the composer scope a query to collections, nodes or "your apps" — `frontend/app/(main)/chat/components/mention-composer-field.tsx`, `mention-menu.tsx`; `en-US.json` `chat.mention*`
- Sharing is to users and teams with roles (Member etc.) through a share sidebar. I found no public/anonymous link-share strings — `frontend/app/components/share/`; `en-US.json` `shareSidebar.*` (grep for "share link"/"public link" = 0)
- The theme supports light, dark and system appearance — `frontend/CLAUDE.md` (theme-provider)
- The responsive target is 375–428 px mobile up to 1920 px, with a collapsing sidebar and 44 px touch targets — `frontend/CLAUDE.md`

### Inferences
- **Onyx ahead:** multi-model comparison, incognito, memory, a first-class Search mode with a default-mode preference, per-chat filters, and the "inject full doc into next message" interaction in the sources panel. PipesHub has no equivalent strings for incognito, memory or multi-model.
- **PipesHub ahead:** citation-to-block highlighting in a real document viewer, agent strategy modes, tool-approval UX, fork, rich artifact rendering (mermaid, tables, CSV), and composer @-mentions for scoping. Whether PipesHub's search view (`chat/components/search/search-view.tsx`) is a full Search mode or only chat-history search needs a hands-on check.
- Sharing: Onyx's share button suggests link sharing. PipesHub shares to named users and teams only, which is arguably more enterprise-safe but less convenient.

### Gaps
- I could not confirm whether Onyx shows follow-up suggestion chips. Only 2-3 strings matched "follow-up"/"suggest" in en.json.
- I could not confirm Onyx's full keyboard shortcut set for web, beyond Cmd+Enter submit in one component and the desktop/Chrome shortcuts.
- Perceived streaming speed was not measured for either product.

## Agent builder UX (creation, knowledge, tools, instructions, starters, sharing, discovery)

### Takeaway
Onyx uses a **form-based agent editor**: Instructions, Conversation Starters, Knowledge (connectors, document sets, files, cutoff date), Actions, Advanced, Share. Discovery runs through an **"Explore Agents" gallery** with admin-curated **Featured** agents, labels and pinning. PipesHub uses a **visual drag-and-drop canvas** (React Flow) that wires model, knowledge, toolsets, MCP, skills and web search into a core agent node, with a user vs **service agent** identity choice. PipesHub has no gallery page: `/agents` redirects to `/chat`, and discovery happens through the chat sidebar.

### Cited Findings
**Onyx**
- Editor sections: Instructions, Conversation Starters, Knowledge, Actions, Advanced Options, Share. Admins can also feature an agent and add labels. There is a Knowledge Cutoff Date — [onyx:documentation/overview/core_features/agents.mdx](https://docs.onyx.app/overview/core_features/agents)
- "Open **Agents** in the sidebar, then select **Explore Agents** to browse Agents shared with you or to create your own." In EE, admins can restrict who creates agents — same page
- Admin row actions include Set as Featured / Remove Featured and Share. Unlisted agents "don't appear in the explore agents list but remain accessible via direct link" — [onyx:web/src/i18n/messages/en.json](https://github.com/onyx-dot-app/onyx/blob/main/web/src/i18n/messages/en.json) (`admin.agents.*`)
- "New users start with the featured agents pinned" (v4.7) — [changelog](https://docs.onyx.app/changelog)
- Agent Analytics (messages and unique users per day per agent) — [onyx:documentation/admins/analytics/overview.mdx](https://docs.onyx.app/admins/analytics/overview)

**PipesHub**
- The builder is a canvas with draggable palette rows and flow nodes for toolsets, MCP servers, skills and web search, plus an agent core node and custom edges — `frontend/app/(main)/agents/agent-builder/components/` (`agent-builder-canvas.tsx`, `sidebar-draggable-row.tsx`, `toolset-flow-node.tsx`, `mcp-flow-node.tsx`, `sidebar-skills-section.tsx`, `sidebar-web-search-section.tsx`)
- Drop validation messages guide wiring ("dropDuplicateKB", "dropOnlyOneToolGroup", "dropToolsetNotReady", "connectionInvalid") — `en-US.json` `agentBuilder.*`
- The create dialog asks for name and **agent type**: User agent vs Service agent (acts with a service-account identity) — `frontend/app/(main)/agents/components/create-agent-dialog.tsx`; `agentBuilder.userAgent`, `agentBuilder.serviceAgent`; `service-agent-identity-control.tsx`
- The builder carries per-agent credential dialogs for toolsets and MCP servers and an MCP approvals notice — `agent-toolset-credentials-dialog.tsx`, `agent-mcp-credentials-dialog.tsx`, `mcp-agent-approvals-notice.tsx`
- Start message / system prompt defaults exist (`agentBuilder.defaultStartMessage`, `defaultSystemPrompt`) — `en-US.json`
- `/agents` has no gallery: `page.tsx` (25 lines) redirects to `/chat/`. The `agents.createPageDescription` string still reads "This is the dedicated create-agent page. Connect your form or redirect to your agent builder here." and `agents.comingSoon` = "Coming soon" — `frontend/app/(main)/agents/page.tsx`; `en-US.json` `agents`
- Discovery runs through the sidebar "My agents" and "More agents" — `frontend/app/(main)/chat/sidebar/my-agents-section.tsx`, `agents-sidebar.tsx`; `en-US.json` `chat.moreAgents`, `chat.searchAgents`

### Inferences
- **Onyx ahead** on discovery and governance UX: gallery, featured, labels, unlisted, default-pinned featured agents, conversation starters as a first-class field, and per-agent analytics.
- **PipesHub ahead** on power and visual composition: a canvas, per-agent credentials, and service-agent identity. A canvas has a steeper learning curve for non-technical users than a form, and PipesHub offers no templates or gallery to start from.
- The leftover placeholder strings in `agents.*` suggest an unfinished agents landing page, a visible gap if it is ever reached.

### Gaps
- I could not confirm whether PipesHub has conversation-starter chips per agent in the chat landing. Only a default start message string was found.
- I have no screenshots of the Onyx agent editor. Its docs page has a TODO for a screenshot.

## Connector setup UX (steps, OAuth, credentials, sync status, errors, re-index controls)

### Takeaway
Both have catalog → configure → authorize → sync flows with status views. Onyx v5.0 raised the bar with **named pre-flight "Test Connection" checks**, **accounts shared across related sources**, **plan-and-apply edits** that run only the needed backfill or prune, a **rebuilt Indexing Status page** with throughput charts, and **loud error banners** for failing connectors. PipesHub has tabbed authenticate/authorize/configure flows, personal vs team connectors, record selection, sync strategies and intervals, last-run summaries and action-item callouts. I found no equivalent of pre-create named checks or plan-preview edits.

### Cited Findings
**Onyx**
- "Test Connection" card with named checks ("Sign in to Jira", "List projects", "Run JQL query") for 12 sources. Checks are dependency-ordered, and "a denial names the exact scope or permission to add". Checks fill pick-lists (Confluence spaces, Slack channels, shared drives) — [changelog v5.0.0](https://docs.onyx.app/changelog) "Connector Checks"
- One account serves related sources (Atlassian, Google, a single Entra app for SharePoint/OneDrive/Outlook/Teams), with a "Share This Account" setting — [changelog v5.0.0](https://docs.onyx.app/changelog) "Accounts Shared Across Sources"
- Edit through a plan: "Review changes" shows what changed and the work it will run. A new Slack channel backfills only that channel. A "Pending backfills" card tracks progress — [changelog v5.0.0](https://docs.onyx.app/changelog) "Editing Connectors Through a Plan"
- Indexing Status lists every connector with its latest run, status counts, search, a cross-connector throughput chart and an Activities card. Per-connector feeds cover indexing, permission sync, checks and prunes, plus failed documents with stack traces and **Resolve**, and a **Manage Index** menu (Update Index, Full Re-index, Pause, Delete) — [changelog v5.0.0](https://docs.onyx.app/changelog) "Indexing Status, Rebuilt"
- "Failing connectors are loud now": error banners for admins with a link to the connector, which clear on recovery (v4.7) — [changelog](https://docs.onyx.app/changelog)
- Document Access is chosen per connector (Public / Specific Groups / Sync with the source), and Connector Access roles are Operator / Editor — [onyx:documentation/admins/connectors/overview.mdx](https://docs.onyx.app/admins/connectors/overview)
- About 80 official per-connector setup guides — `onyx:documentation/admins/connectors/official/` (file listing)
- The connector catalog supports keyboard navigation (v5.0 Admin and UI) — [changelog](https://docs.onyx.app/changelog)

**PipesHub**
- Connector panel components: catalog layout, details layout, auth card, authenticate tab, authorize tab, configure tab, schema-driven form fields, select-records page, config success dialog, team-visibility warning, disable-first dialog, admin-access-required dialog — `frontend/app/(main)/workspace/connectors/components/`
- Instance view: status badges, overview tab with stats shimmer, last-run summary, action-items callout, code-graph status, settings tab — `frontend/app/(main)/workspace/connectors/components/instance-card/`, `instance-panel/`
- Controls and strings: full sync (with confirm), stop sync (with confirm), sync intervals, sync strategies, refresh instance or all instances, filters, presets, vector-store actions — `en-US.json` `workspace.connectors.*` (`fullSync`, `fullSyncConfirm`, `stopSyncConfirm`, `syncIntervalOptions`, `syncStrategies`, `refreshAllInstances`, `presets`)
- Re-index safeguards: `reindexScope`, `forceReindexConfirm`, `reindexBlocked` top-level i18n namespaces — `frontend/lib/i18n/locales/en-US.json`
- Personal vs team connectors (`workspace/connectors/personal`, `workspace/connectors/team`), and a separate "Your connectors" nav entry — `workspace.sidebar.nav.yourConnectors`
- Desktop local-folder sync connector (`localFsDesktop`) — `en-US.json` `workspace.connectors.localFsDesktop`; `frontend/electron/local-sync`
- Open issue: "Snowflake connector is implemented but missing from the registry, UI, and documentation" (#3752) — [PipesHub issues](https://github.com/pipeshub-ai/pipeshub-ai/issues/3752)

### Inferences
- **Onyx ahead** on connector setup quality: named checks before create, scope-precise error messages, plan-preview on edit, cross-connector observability and proactive failure banners. These are the features that cut "it silently failed on first index" support load.
- **PipesHub ahead or different** on personal (per-user) connectors, record selection, a desktop local-folder sync, and per-instance action items. Personal connectors are self-serve UX that Onyx handles only for federated Slack.

### Gaps
- I did not count the clicks needed to set up a common connector (e.g. Google Drive OAuth) in either product, so step counts are not quantified.
- I could not see PipesHub error-message copy for a failed OAuth from the code skim alone. `workspace.connectors.errors` exists but was not read.

## Admin console (navigation, users, groups, analytics, settings, branding)

### Takeaway
Onyx's Admin Panel is broader and more commercial. It has **usage/spend dashboards, analytics (queries, feedback, Slack, agents), query history, report exports**, full **white-labeling** (logo, name, greeting, header/footer, login subtitle, announcements, first-visit consent), and locked-with-upgrade-prompt pages. PipesHub's Workspace settings cover identity and configuration well (users, groups, teams, authentication, AI models, connectors, MCP, bots, prompts, skills, service accounts, OAuth apps, PATs, web search, mail, labs). It has **no analytics or usage dashboard**, and branding is limited to company logo and display name.

### Cited Findings
**Onyx**
- Admin Panel sections: Language Models, Connectors, Plugins, Agents, Slack Integration, User Management, Usage & Analytics, Advanced Configs, Plans & Billing. Higher-edition pages "stay visible in the sidebar with a lock" — [onyx:documentation/admins/overview.mdx](https://docs.onyx.app/admins/overview)
- Admin route folders include agents, billing, bots, chat-preferences, code-interpreter, connectors, document-processing, groups, indexing-status, language-models, llm-gateway, mcp-actions, scim, security, service-accounts, sso-providers, token-rate-limits, tracing, users, voice, web-search, plus EE theme, performance, standard-answer and export-logs — `onyx:web/src/app/admin/`, `onyx:web/src/app/ee/admin/` (directory listing)
- Usage: active users, workspace spend, tokens, top spender, a per-user spend table with drill-down, and system usage. Analytics: queries and users per day, feedback trend, Slack channels, Agent Analytics. Usage reports are a ZIP with a PDF and CSVs. Query History review and export are available. Plans: Business/Enterprise/Cloud — [onyx:documentation/admins/analytics/overview.mdx](https://docs.onyx.app/admins/analytics/overview)
- Each user sees their own usage under User Settings → Usage — same page; [changelog v4.6](https://docs.onyx.app/changelog) "Usage and Cost Management"
- White labeling: logo, display name, logo display style, greeting message, chat header text, chat footer (Markdown), login page subtitle, custom help link, system announcement (optional pop-up), first-visit notice with consent — [onyx:documentation/admins/advanced_configs/white_labeling.mdx](https://docs.onyx.app/admins/advanced_configs/white_labeling)
- Fully group-based permissions with Group Permissions on the group pages. Guardrails: the last admin cannot be removed and self-demotion is refused (v4.7) — [changelog](https://docs.onyx.app/changelog)
- "Plan-limited settings are shown locked with an upgrade prompt instead of hidden" (v5.0) — [changelog](https://docs.onyx.app/changelog)

**PipesHub**
- Workspace sidebar sections: Overview, Workspace, People, Personal, Developer settings. Nav items: general, users, groups, teams, authentication, AI models, connectors, MCP servers, actions, bots, prompts, services, web search, mail, labs, archived chats, profile, your connectors/actions/MCP servers/skills, service accounts, OAuth, personal access tokens, API documentation — `en-US.json` `workspace.sidebar.nav.*`, `workspace.sidebar.sections.*`
- General settings: company logo (256 px), registered name, display name, contact email, address, and a data-collection toggle. No greeting, theme, footer or announcement controls — `en-US.json` `workspace.general`
- "Labs" (platform configuration) currently holds a file-upload size limit — `en-US.json` `workspace.labs`
- No analytics, usage or spend page: no such nav item, and no analytics/usage/dashboard matches in `frontend/app/(main)/workspace/**/*.tsx` (grep)
- Groups have a permissions sub-page; there are also teams, tenants and orgs pages — `frontend/app/(main)/workspace/groups/permissions`, `workspace/teams`, `workspace/tenants`, `workspace/orgs`
- Open issue: "The login and org_created telemetry events carry the user's email address" (#3302) — [PipesHub issues](https://github.com/pipeshub-ai/pipeshub-ai/issues/3302)

### Inferences
- **The biggest admin gap for PipesHub is analytics.** Adoption, feedback trends, per-agent usage, spend and query history are what admins use to justify rollout. PipesHub already collects thumbs-up/down with categories (chat feedback strings) but surfaces no admin view of them.
- **Branding gap:** Onyx's white-label surface, especially the first-visit consent notice and announcements, is what regulated buyers ask for. PipesHub has logo and name only.
- PipesHub's people model (users, groups, teams, service accounts) and developer settings (OAuth apps, PATs, API docs) are at least as rich as Onyx's.

### Gaps
- I could not view the PipesHub user-management screens' bulk actions or invite flow in detail (`workspace.bulk` exists).
- I could not confirm whether PipesHub plans an analytics page.

## Surfaces beyond web (Slack/Teams bot, Chrome extension, desktop, launchers, mobile)

### Takeaway
Onyx ships more surfaces: Slack bot (per-channel config, "Continue in Web UI"), Discord bot, **Chrome extension** (new tab, side panel, selection search, omnibox), **desktop app** (Tauri, global summon shortcut), **native mobile app** (Expo), **website widget**, CLI, MCP server and a Salesforce integration. PipesHub has a bot platform (Slack, Discord, Telegram, GitHub bots mappable to agents), an Electron desktop app with local-folder sync, and an MCP endpoint. I found no Chrome extension, native mobile app or embeddable widget for PipesHub.

### Cited Findings
**Onyx**
- Slack: multiple Slack apps, each linked to agents. Per-channel behavior (tag-only or all messages, knowledge scope, ephemeral replies, follow-up tagging), citations, "Continue in Web UI". Socket Mode, so no public webhook is needed — [onyx:documentation/overview/onyx_anywhere/slack.mdx](https://docs.onyx.app/overview/onyx_anywhere/slack)
- Chrome extension: Onyx on every new tab, side panel ("Read this tab"), selection search, address bar (`onyx` + Tab), shortcuts (Cmd+Shift+O, Ctrl+O), and managed-policy lockdown — [onyx:documentation/overview/onyx_anywhere/chrome_extension.mdx](https://docs.onyx.app/overview/onyx_anywhere/chrome_extension)
- Desktop app: Windows/macOS/Linux installers, summon shortcut Cmd+Shift+Space / Ctrl+Alt+Space, multiple windows, a menu-bar icon and a keyboard shortcut table — [onyx:documentation/overview/onyx_anywhere/desktop_app.mdx](https://docs.onyx.app/overview/onyx_anywhere/desktop_app); source `onyx:desktop/src-tauri`
- Other surfaces: Discord bot, CLI, MCP server, website widget — `onyx:documentation/overview/onyx_anywhere/{discord_bot,cli,mcp_server,website_widget}.mdx`. Native mobile app (Expo SDK 57) with model picker and actions sheet — `onyx:mobile/`; [changelog v4.6/v4.8 Mobile](https://docs.onyx.app/changelog)

**PipesHub**
- Bots page: Slack, Discord, Telegram and GitHub bots, each optionally mapped to an agent ("Configure bot credentials and optionally map each bot to an agent") — `en-US.json` `workspace.bots`; `frontend/app/(main)/workspace/bots/`
- Slack bot code lives in `backend/nodejs/apps/src/integrations/slack-bot/` and builds citations with text fragments — `backend/nodejs/apps/src/integrations/slack-bot/src/utils/citations.ts`
- Electron desktop app with server-URL setup ("Connect to PipesHub server"), deep links and local-folder sync — `frontend/electron/` (`main.ts`, `deep-link.ts`, `local-sync/`); `en-US.json` `electron.serverUrlSetup`
- MCP at `/mcp` on the same origin as the UI — `AGENTS.md` ("Where the UI listens")
- No Chrome extension, mobile app or widget directory exists at the repo root (`ls /home/user/pipeshub-ai`: `integrations/` holds only `omnigent`, `slack-bot`)

### Inferences
- **Onyx ahead** on "Onyx anywhere": the Chrome extension and desktop summon shortcut are high-frequency entry points that build daily habit. A native mobile app and a web widget widen reach.
- **PipesHub ahead** on bot breadth (Telegram and GitHub bots) and on desktop local-file sync, a capability rather than just a wrapper.

### Gaps
- I could not confirm whether either product ships a Microsoft Teams bot. Onyx indexes Teams as a connector; I found no Teams bot doc. Raycast and other launchers were not found for either.

## Accessibility, i18n, performance perception

### Takeaway
Both are translated. Onyx: 9 UI languages, including Arabic with full **RTL mirroring**, a workspace default language with per-user override, CJK line-breaking, and a v4.7 pass of 211 keyboard-accessibility fixes. PipesHub: 10 locale files, 4 of them English/Chinese regional variants (en-US, en-IN, zh-CN, zh-TW, zh-SG), plus de, es, hi, ko, cs, all CI-gated for parity. PipesHub lacks French, Japanese, Portuguese and any RTL language.

### Cited Findings
- Onyx locales: en, es, pt, fr, de, ja, zh, ko, ar — [onyx:web/src/i18n/config.ts](https://github.com/onyx-dot-app/onyx/blob/main/web/src/i18n/config.ts)
- Onyx v4.7: "fully translated into Spanish, Portuguese, French, German, Japanese, Simplified Chinese, and Korean… chat, settings, authentication, and the entire admin panel". CJK font and kinsoku line-breaking — [changelog v4.7.0](https://docs.onyx.app/changelog) "Onyx in Your Language"
- Onyx v4.8: Arabic "brings full right-to-left support… The whole layout mirrors". Per-paragraph direction in messages. The model is told the interface language — [changelog v4.8.0](https://docs.onyx.app/changelog) "Arabic, and a Right-to-Left Interface"
- Onyx v5.0: workspace-wide UI language in Chat Preferences with a per-user override — [changelog v5.0.0](https://docs.onyx.app/changelog)
- Onyx v4.7: "Every clickable surface is now keyboard-operable (211 accessibility fixes)" — [changelog](https://docs.onyx.app/changelog)
- PipesHub locales: en-US, en-IN, de-DE, es-ES, hi-IN, ko-KR, zh-CN, zh-TW, zh-SG, cs-CZ, gated by `locale-policy.json` and `npm run i18n:check` — `frontend/CLAUDE.md`; `frontend/lib/i18n/locales/`
- A rough proxy for ARIA attribute coverage: `aria-` appears 644 times in PipesHub `frontend/app/**/*.tsx` and 756 times in Onyx `web/src/**/*.tsx` (grep counts, my measurement; not a quality measure)
- PipesHub hard-codes English role labels in the onboarding survey — `frontend/app/(main)/components/surveys/user-background/user-background-survey.tsx` lines 19-30

### Inferences
- **Onyx ahead** on i18n reach (fr/ja/pt/ar, RTL) and on an explicit accessibility programme. PipesHub's Hindi and Czech are unique to it.
- For performance perception, there is no comparable data. Onyx's changelog repeatedly cites UI performance work (v2.2 "UI Performance and Polish"). PipesHub's 0.6.0 cites a "query service ~1.8× faster" (`CHANGELOG.md` table) — backend, not UI.

### Gaps
- No Lighthouse or axe audits were run for either product, and no WCAG conformance statement was found for either.

## Public UX praise and complaints

### Takeaway
Independent user sentiment is thin for both. Onyx has public HN launch discussion and third-party guides. PipesHub has almost no independent reviews: Gartner lists "No Reviews Yet", and its Reddit launch post drew about 5 comments. PipesHub's own issue tracker shows onboarding-adjacent trust issues around default telemetry.

### Cited Findings
- Onyx's founders' HN launch says users mostly wanted to chat with LLMs, prompting a pivot to a chat-focused UI — [Launch HN: Onyx (YC W24)](https://hn.svelte.dev/item/46045987)
- Third-party notes: Onyx "Lite" mode under 1 GB memory. Operators must manage OpenSearch heap, TLS and version pinning when self-hosting on Railway — [wz-it.com guide](https://wz-it.com/en/blog/onyx-self-hosted-installation/); [Railway](https://railway.com/deploy/onyx-ai)
- Open WebUI positions Onyx as focused on internal knowledge with permission-aware retrieval — [docs.openwebui.com/alternatives/onyx](https://docs.openwebui.com/alternatives/onyx)
- PipesHub: Gartner shows no reviews, and the directory listing names Onyx as the top open-source alternative — [Gartner](https://www.gartner.com/reviews/product/pipeshub-498905425); [openalternative.co](https://openalternative.co/alternatives/pipeshub)
- PipesHub issues touching UX trust: #3625 (data collection on by default without notice), #3299 (no telemetry opt-out before the first push), #3302 (telemetry events carry email) — [GitHub issues](https://github.com/pipeshub-ai/pipeshub-ai/issues)

### Inferences
- With so little public feedback, the code and docs comparison above is the stronger evidence. PipesHub's telemetry-default issues are the most concrete first-run trust risk on record.

### Gaps
- I could not search Onyx GitHub issues by UI/UX label (no API access) and found no Reddit threads with user UX opinions on either product. YouTube demos were not reviewed.
