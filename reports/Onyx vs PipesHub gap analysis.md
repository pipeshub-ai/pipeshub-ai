# PipesHub out-builds Onyx but trails on trust

Onyx leads PipesHub in four areas: how easy it is to run, enterprise governance, the number of places users can reach it, and distribution. It does not lead on the core retrieval engine. Most of Onyx's governance lead sits behind its paid licence. Its free Community Edition offers no document permission sync, no groups, no analytics and no credential encryption ([Enterprise Edition](https://docs.onyx.app/admins/getting_started/enterprise_edition), [encryption.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/utils/encryption.py)). The free PipesHub build already syncs per-record access rights for its major SaaS connectors and encrypts stored credentials ([connector_factory.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/connectors/core/factory/connector_factory.py), [encryption.service.ts](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/nodejs/apps/src/libs/services/encryption.service.ts)).

Onyx's real leads are elsewhere:
- **Deployment.** A 2 GB "Lite" mode, a Go CLI that handles install and upgrade, Terraform for three clouds, and per-worker autoscaling with Grafana dashboards.
- **Enterprise checklist.** SCIM, a SIEM audit stream, spending limits, white-labelling, and SOC 2 Type II for its cloud.
- **Surfaces.** A Chrome extension, a website widget, Discord, a CLI and an LLM gateway.
- **Reach.** About **33.1k GitHub stars against 3.9k**, named enterprise logos, a $10M seed, and a Zoom acquisition on 23 September 2026.

The users of both products want the same things: one search across their tools, citations they can check, and self-hosting. What they complain about also matches: heavy installs and connectors that fail silently. That makes PipesHub's fastest win **trust, not features**. PipesHub needs to:
- fix telemetry consent and its unanswered security inbox;
- ship fixes as patch releases;
- prove that its permission sync is correct;
- launch 1.0 publicly.

It should then close the operations and governance gaps over twelve months while keeping its lead in permission-aware retrieval, knowledge and code graphs, block-level citations, and agent/MCP depth. Much of the competitive evidence is weak, because onyx.app, pipeshub.com, docs.pipeshub.com, techcrunch.com, Reddit and Hacker News could not be fetched directly. This report marks every claim that rests on search snippets.

## Evidence quality: the code is solid, the websites were blocked

The comparison is strongest where it rests on code. Onyx moved its documentation source into its main repository in v5.0.2, so a clone at commit `3eb6420` (1 November 2026) gave researchers **410 Mintlify pages** and a changelog covering v2.0 to v5.0. Those docs pages are what docs.onyx.app publishes, even though the live site was blocked ([GitHub releases](https://github.com/onyx-dot-app/onyx/releases), [docs tree](https://github.com/onyx-dot-app/onyx/tree/main/documentation)). PipesHub was read from its own checkout and from the GitHub API, which returned all 314 issues and the release list ([releases](https://github.com/pipeshub-ai/pipeshub-ai/releases)).

The sites below were unreachable. Every claim that depends on them comes from search-engine snippets or third-party aggregators, and this report treats them as **unverified**.

| Claim class | Source actually used | Reliability |
| --- | --- | --- |
| Onyx features, editions, deployment, changelog | Docs source and code in the Onyx repo | High |
| PipesHub features, editions, deployment | Local code, CHANGELOG, GitHub API | High |
| GitHub issue, star and release statistics, both products | GitHub API and search (PipesHub); `gh` search plus git tags (Onyx). Onyx's REST API was denied. | High for PipesHub, medium for Onyx |
| Onyx pricing (~$20/user/month annual, $25 monthly) | Aggregators: [aitoolsatlas](https://aitoolsatlas.ai/tools/onyx/pricing), search summaries of [onyx.app/alternatives/glean](https://onyx.app/alternatives/glean). onyx.app was blocked. | **Unverified** |
| Onyx customer logos (Netflix, Ramp, Thales, Bitwarden, NASA) | Press release ([GlobeNewswire](https://www.globenewswire.com/news-release/2025/03/12/3041638/0/en/Onyx-Raises-10M-To-Help-Teams-Find-Knowledge-Faster-with-Open-Source-AI-Agents.html)), [Dealroom](https://dealroom.co/news/156435-zoom-buys-ai-search-startup-onyx-to-win-data-sovereignty-customers/). The "Ramp chose Onyx for reliability" line is a snippet only. | Medium. Logos are the vendor's own claims. |
| Onyx ISO 27001 and HIPAA | Aggregator only ([trustlists.org](https://trustlists.org/company/onyx/)). Onyx's docs claim only SOC 2 Type II and GDPR ([Compliance](https://docs.onyx.app/security/onyx_cloud/compliance)). | **Unverified** |
| Hacker News and Product Hunt quotes and points | Search snippets of news.ycombinator.com; PH statistics via [hunted.space](https://www.hunted.space/product/onyx-8/launches/onyx-10). TechCrunch could not be fetched. | **Unverified wording** |
| PipesHub pricing, customers, cloud SOC 2 | No public price found. One snippet called PipesHub Cloud "SOC 2 certified"; a separate search found no attestation. | **Unverified and contradictory** |
| PipesHub funding ($250K pre-seed, AI2 Incubator) | One snippet of [EveryDev.ai](https://www.everydev.ai/tools/pipeshub) | **Unverified** |
| PipesHub's own "vs Onyx" and "vs Glean" pages | Snippets of [pipeshub.com/pipeshub-vs-onyx](https://pipeshub.com/pipeshub-vs-onyx) and [pipeshub-vs-glean](https://pipeshub.com/pipeshub-vs-glean) | **Unverified** |

Four inconsistencies inside the research matter for the plan:

1. **PipesHub's Discord, Telegram and GitHub bots are not shipped.** One note counted them as an advantage, but in code they are disabled "coming soon" stubs ([bot-config-panel.tsx](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/workspace/bots/components/bot-config-panel.tsx)). Only the Slack bot is real.
2. **PipesHub's "Onyx relies on vector retrieval" line is out of date.** Onyx now runs hybrid vector-plus-keyword search on OpenSearch, with LLM query expansion ([Search API guide](https://docs.onyx.app/developers/guides/search_api_guide)).
3. **Onyx's own docs disagree about Craft.** Some pages say Craft is in "all editions"; the admin page and the open-source statement say it needs a paid plan ([Craft](https://docs.onyx.app/overview/core_features/craft), [Admin Craft](https://docs.onyx.app/admins/managing_features/craft)).
4. **PipesHub cannot claim "no open-core split".** It is open core too: groups, SAML, organisation-wide tool-approval rules and service-account delegation sit in a closed Enterprise Edition ([groupsEnterprisePlaceholder.tsx](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/workspace/groups/groupsEnterprisePlaceholder.tsx), [en-US.json](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/lib/i18n/locales/en-US.json)).

## Onyx's lead is operational, commercial and distributional

The table compares the two products area by area. The "Onyx edition" column matters, because many of Onyx's leads disappear for a self-hoster comparing free builds.

| Area | Onyx (Nov 2026) | PipesHub (1.0.0 + main) | Leader | Onyx edition |
| --- | --- | --- | --- | --- |
| Smallest footprint | Lite: 2 vCPU / 2 GB, chat only, no indexing; Standard: 4 vCPU / 10 GB minimum | 16 GB host; 8 GB "eval" profile | Onyx | All |
| Install and day-2 tooling | Go CLI with install, upgrade, status, logs and offline modes; PowerShell; bundled nginx with Let's Encrypt | 2,229-line Bash wizard; bring your own TLS; CLI requested in #3074 | Onyx | All |
| Infrastructure as code | Terraform for AWS, GCP and Azure; CloudFormation; Terraform provider | Helm plus an AWS EKS guide | Onyx | All |
| Scaling and observability | HPA and KEDA per Celery worker; Prometheus on every service; four Grafana dashboards | Whole monolith pod scales as one; Prometheus in 2 files; no OpenTelemetry | Onyx | All |
| Upgrades | Parallel patch lines (v4.8.10, v4.9.6, v5.0.3); 499 Alembic migrations | Release gate promotes by digest after upgrade tests; no patch branches | Mixed | All |
| SSO | One provider of any type in CE (SAML on self-hosted); multiple providers on paid plans | Password, OTP, Google, Microsoft; SAML and OIDC removed from OSS in 1.0 | Onyx | CE |
| SCIM, audit stream, spending limits, retention | Yes | None found in OSS | Onyx | Paid |
| Analytics and query history | Usage, spend, feedback, per-agent statistics, CSV export | No admin analytics page | Onyx | Paid |
| White-labelling | Logo, greeting, header and footer, first-visit consent notice, announcements | Logo and display name | Onyx | Paid |
| Document permission sync | 16 connectors | Record-level ACLs on most SaaS connectors | **PipesHub** | Paid in Onyx, free in PipesHub |
| Credential encryption at rest | Enterprise only; the MIT build stores plaintext | Free, keyed by `SECRET_KEY` | **PipesHub** | Paid |
| Chat extras | Multi-model compare, incognito, memory, Search mode, per-chat filters, public share links | Fork, block-level citation viewer, agent strategies, tool-approval cards | Split | Mostly CE; Search mode is paid |
| Agent discovery | Explore gallery, featured agents, conversation starters, per-agent analytics | Canvas builder; `/agents` redirects to chat | Onyx | Mostly CE |
| Automation | Craft Scheduled Tasks | `ENABLE_WORKFLOW_BUILDER` flag only; no schedules | Onyx | Disputed |
| Connectors | About 58 in the registry; preflight checks; plan-and-apply edits; rebuilt status page | 48 registered types (about 33 products); 60-minute polling | Onyx on breadth and operations | CE |
| Surfaces | Chrome extension, widget, Discord, CLI, desktop, Expo mobile (development build), LLM gateway | Slack bot, Electron desktop with local sync, MCP | Onyx | Mostly CE; gateway is paid |
| Documentation | 410 pages, docs CI, 35+ deployment pages | 22 + 4 in-repo docs; the docs site could not be checked | Onyx (provisional) | — |
| Distribution | 33.1k stars, about 940 a month; Launch HN 254 points; Zoom-owned | 3,899 stars, about 200–250 a month; no HN or PH launch found | Onyx | — |

Sources for the table:
- Onyx deployment and operations: [Onyx resourcing](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/getting_started/resourcing.mdx), [Onyx CLI](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/local/onyx_cli.mdx), [Terraform modules](https://github.com/onyx-dot-app/onyx/tree/main/deployment/terraform/modules), [Helm templates](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx/templates).
- PipesHub deployment: [ADVANCED_DEPLOYMENT.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/ADVANCED_DEPLOYMENT.md), [process_monitor.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/process_monitor.sh).
- Identity, governance and editions: [SSO providers](https://docs.onyx.app/deployment/authentication/sso_providers), [upgrading-to-1.0-sign-in.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/upgrading-to-1.0-sign-in.md), [Connectors overview](https://docs.onyx.app/admins/connectors/overview), [Changelog](https://docs.onyx.app/changelog).
- Stars and growth: [Onyx repo](https://github.com/onyx-dot-app/onyx), [PipesHub repo](https://github.com/pipeshub-ai/pipeshub-ai).

### Running PipesHub costs more memory and more operator effort

The deployment gap is the one users feel first. PipesHub runs Node, eight Python services and the Slack bot inside **one container managed by a shell process monitor**. Every service therefore shares one memory budget, and nothing can be scaled on its own. The Helm chart can only add whole copies of the monolith ([process_monitor.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/process_monitor.sh), [values.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/values.yaml)). Onyx ships HPA and KEDA templates for each of its eight worker types, and Prometheus endpoints with Grafana dashboards for indexing, pruning, OpenSearch latency and Redis queues ([dashboards](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx/dashboards), [monitoring.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/production/monitoring.mdx)).

Three smaller gaps add to this:
- **TLS.** PipesHub's README warns that a deployment served over plain HTTP shows a "white screen", and tells users to bring their own TLS terminator. Onyx bundles nginx with Let's Encrypt ([README](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md), [Onyx compose](https://github.com/onyx-dot-app/onyx/tree/main/deployment/docker_compose)).
- **Chart version lag.** The PipesHub Helm chart still declares appVersion 0.10.0 while the app is at 1.0.0 ([Chart.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/Chart.yaml)).
- **No patch lane.** Without patch branches, a fix can sit unreleased on `main` while the published image is broken. Issue #3030 documented exactly that: the published `:slim` image crashed the whole Python backend on a default install ([#3030](https://github.com/pipeshub-ai/pipeshub-ai/issues/3030)).

PipesHub is ahead on one operational point, **verifying the artifact it publishes**. Its release gate installs and upgrades on amd64 and arm64, then promotes the image by digest ([RELEASE.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/RELEASE.md)). Its test suite is also larger: about 2,543 Python test files against Onyx's roughly 1,820 ([CONTRIBUTING.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CONTRIBUTING.md)).

### The enterprise checklist is Onyx's paid wall, and PipesHub has thinned its own

During 2026 Onyx closed most of the procurement checklist: SCIM 2.0 (v3.0), a SIEM-ready audit stream mapped to SOC 2 CC7 (v4.3), multi-provider SSO in the admin panel (v4.4), group RBAC (v4.7), spending limits by token or dollar (v4.6), flexible chat retention (v4.2), and **SOC 2 Type II and GDPR** for its cloud ([Audit Logs](https://docs.onyx.app/admins/auditing/audit_logs), [Spending Limits](https://docs.onyx.app/admins/advanced_configs/spending_limits), [Compliance](https://docs.onyx.app/security/onyx_cloud/compliance)). PipesHub's open-source build has **no admin analytics page, no audit-log UI and no SCIM endpoint**. Its branding stops at a logo and a display name ([en-US.json](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/lib/i18n/locales/en-US.json)).

PipesHub 1.0 also went backwards on one item. It **removed SAML and generic OIDC from the open-source build** ([changelog/1.0.0.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/changelog/1.0.0.md)). Onyx CE still allows one SSO provider of any type, including SAML on self-hosted installs ([SAML](https://docs.onyx.app/deployment/authentication/saml)). An evaluator who needs Okta or generic OIDC therefore now gets more from free Onyx than from free PipesHub.

### Onyx sands down the rough edges of chat, agents and connectors

The chat products are close to parity. Onyx's extras are:
- **multi-model side-by-side answers** (v3.2);
- **incognito chat** (v4.6);
- user-editable **memory**;
- a separate **Search mode** that returns source cards (paid tier only);
- **search filters stored per chat** (v4.9);
- **durable runs** that keep going on the server after the browser disconnects (v4.2);
- public share links.

Sources: [Chat UI](https://docs.onyx.app/overview/core_features/chat), [Changelog](https://docs.onyx.app/changelog). PipesHub has a memory tool in `agent_loop_lib` that nothing imports, so memory is not a product feature ([pipeshub features](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend/python/app/agent_loop_lib)). PipesHub shares chats only with named users and teams.

For agents, Onyx offers an **Explore gallery** with featured agents, pinned automatically for new users, plus conversation starters and per-agent analytics ([Custom Agents](https://docs.onyx.app/overview/core_features/agents)). PipesHub's `/agents` page simply redirects to chat. The page's i18n string still reads "Connect your form or redirect to your agent builder here" ([agents/page.tsx](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/agents/page.tsx)). Onyx automates work through **Craft Scheduled Tasks** ([Scheduled Tasks](https://docs.onyx.app/overview/core_features/craft_scheduled_tasks)). PipesHub has only a workflow-builder feature flag with no UI behind it, and no scheduled agents ([featureflag.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/services/featureflag/featureflag.py)).

On connectors, Onyx has about 58 in its registry against PipesHub's **48 registered types, which cover roughly 33 distinct products** ([registry.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/registry.py), [connector_factory.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/connectors/core/factory/connector_factory.py)). Count matters less than how connectors are run. Onyx v5.0 added:
- named **preflight checks** that state the exact missing scope;
- one account shared across related sources;
- **plan-and-apply edits** that backfill only what changed;
- a rebuilt Indexing Status page with retry for individual failed documents.

Onyx v4.7 already made failing connectors raise loud admin banners ([Changelog v5.0.0](https://docs.onyx.app/changelog)). These features target the PipesHub failure mode users report most: **syncs that report success but are incomplete**. One Notion sync logged "completed successfully" after syncing 40 of 1,175 pages ([#2994](https://github.com/pipeshub-ai/pipeshub-ai/issues/2994)).

### Onyx is present wherever users already work

Onyx ships a Chrome extension (new tab, side panel, selection search, omnibox), a website widget of about 100–150 KB, a Discord bot, a Go CLI, a Tauri desktop app with a global shortcut, and an OpenAI- and Anthropic-compatible **LLM gateway** that Claude Code and Codex can point at (paid tier) ([Chrome extension](https://docs.onyx.app/overview/onyx_anywhere/chrome_extension), [Website widget](https://docs.onyx.app/overview/onyx_anywhere/website_widget), [LLM Gateway](https://docs.onyx.app/developers/guides/llm_gateway)). PipesHub has the Slack bot, an Electron desktop app with local-folder sync, and its MCP server. It has no browser extension, widget or mobile app ([electron](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/frontend/electron)).

**Neither product has a Microsoft Teams bot.** Onyx's bot code covers only Slack and Discord ([Slack](https://docs.onyx.app/overview/onyx_anywhere/slack)). PipesHub has Teams only as an agent toolset. Given how deep PipesHub's Microsoft 365 connectors go, a Teams bot is an opening to lead rather than catch up.

### The distribution gap is about 8.5 times, and Onyx is still widening it

Onyx has **33,140 stars and 4,684 forks** against PipesHub's **3,899 and 631**. Onyx is gaining roughly 940 stars a month, about a quarter of PipesHub's lifetime total every month ([Onyx repo](https://github.com/onyx-dot-app/onyx), [star-history](https://star-history.com/onyx-dot-app/onyx)). Its launch history includes:
- Launch HN for Danswer in 2024 (231 points) ([HN 39467413](https://news.ycombinator.com/item?id=39467413));
- a Launch HN for Onyx in late 2025 (254 points and 160 comments, per snippets) ([HN 46045987](https://news.ycombinator.com/item?id=46045987));
- a Product Hunt launch (159 upvotes, per an aggregator, unverified);
- TechCrunch coverage of its $10M seed from Khosla and First Round ([GlobeNewswire](https://www.globenewswire.com/news-release/2025/03/12/3041638/0/en/Onyx-Raises-10M-To-Help-Teams-Find-Knowledge-Faster-with-Open-Source-AI-Agents.html)).

Its marketing site runs a complete search funnel: an alternatives hub, an "open-source Glean alternative at a fraction of the cost" page, a Glean cost calculator, and a Glean-alternatives listicle that also ranks for PipesHub's queries ([onyx.app/alternatives/glean](https://onyx.app/alternatives/glean), [cost calculator](https://onyx.app/glean-cost-calculator); all snippet-sourced).

The research found **no PipesHub HN or Product Hunt launch, no public pricing and no named customers**. The main press item is a heise.de story on 1.0 ([heise.de](https://www.heise.de/en/news/AI-agents-on-a-short-leash-PipesHub-searches-company-knowledge-11503145.html)). Gartner lists PipesHub with "No Reviews Yet" ([Gartner](https://www.gartner.com/reviews/product/pipeshub-498905425)).

The Zoom acquisition (23 September 2026) gives Onyx vendor stability. It also raises open questions about packaging that analysts have already noted. Zoom says Onyx "will remain a standalone platform and stay open source" ([Zoom newsroom](https://news.zoom.com/zoom-acquires-onyx-adding-open-source-enterprise-knowledge-search-to-zoom-ai-services/), [remio.ai](https://www.remio.ai/post/zoom-onyx-acquisition-targets-a-gap-cloud-first-ai-cannot-ignore)). No community reaction to the deal was found, so any "independence" message from PipesHub is a bet, not a measured opening.

## PipesHub already wins where enterprise buyers feel risk

PipesHub's strengths cluster in the areas enterprises worry about most: who can see what, whether answers can be checked, and whether agents can be trusted with tools. Several of them answer demand that shows up in Onyx's own issue tracker.

| PipesHub strength | Evidence | Matching Onyx gap or user demand |
| --- | --- | --- |
| **Free per-record permission sync** for Drive and Gmail Workspace, SharePoint, OneDrive, Confluence, Jira (Cloud and Data Center), Slack, Box, Salesforce and others | [connector_factory.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/connectors/core/factory/connector_factory.py), [arangodb.py constants](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/config/constants/arangodb.py) | Onyx permission sync is Business+ only ([Connectors overview](https://docs.onyx.app/admins/connectors/overview)) |
| **Credential encryption in the free build** | [encryption.service.ts](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/nodejs/apps/src/libs/services/encryption.service.ts) | The Onyx MIT build logs "does not support encryption of secrets" ([encryption.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/utils/encryption.py)) |
| **Block-level citations** with text-fragment deep links and a highlighting document viewer | [docs/text-fragments.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/text-fragments.md) | Onyx #4786, "PDF answers should link to exact pages" ([#4786](https://github.com/onyx-dot-app/onyx/issues/4786)) |
| **Code graph** (1.0) with MCP code-navigation tools | [changelog/1.0.0.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/changelog/1.0.0.md) | Onyx #4025, "index source code" (18 reactions), and #4717 ([#4025](https://github.com/onyx-dot-app/onyx/issues/4025)) |
| **Knowledge graph and entity layer**, actively developed | [docs/entity-resolution.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/entity-resolution.md) | Onyx v5.0 says its KG pipeline variables "are now ignored" ([Changelog](https://docs.onyx.app/changelog)) |
| **Pluggable storage** for graph, vector, KV and broker | [AGENTS.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/AGENTS.md) | Onyx #1165, "work with other vector database" (11 reactions) |
| **Agent depth**: Quick, Plan & execute and Deep strategies; visual canvas; service agents; tool-approval cards; versioned skills; 31-server MCP catalog; about 40 first-party toolsets | [modes.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/agent_loop/modes.py), [agents/mcp](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend/python/app/agents/mcp) | Onyx concentrates approvals and multi-step work in Craft, which may be paid only |
| **Shared Projects** with members | [CHANGELOG.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CHANGELOG.md) | Onyx #10262: Projects "cannot be shared with others" ([#10262](https://github.com/onyx-dot-app/onyx/issues/10262)) |
| **LLM-gateway identity forwarding** of user and groups | [docs/llm-gateway-identity.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/llm-gateway-identity.md) | Onyx #10461, LiteLLM `user` dropped, still open ([#10461](https://github.com/onyx-dot-app/onyx/issues/10461)) |
| **Developer contract**: a 399-path OpenAPI spec validated in CI, SDK break-approval gate, Python/TypeScript/Go SDKs, MCP with OAuth 2.1, PATs and device flow | [pipeshub-openapi.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml), [README](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md) | Whether Onyx publishes a machine-readable spec or SDKs was not verified |
| **Acme demo dataset** with golden questions, including one restricted by permissions | [demo-data.ts](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/workspace/connectors/demo-data/demo-data.ts) | Onyx onboarding ends with an empty index ([constants.ts](https://github.com/onyx-dot-app/onyx/blob/main/web/src/lib/onboarding/constants.ts)) |
| **Contributor friendliness**: Apache-2.0, no IP assignment, 40 open good-first-issues, median merge about 4.9 hours | [CONTRIBUTING.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CONTRIBUTING.md) | Onyx requires an IP assignment for EE contributions and has 1 open good-first-issue ([contributor_ip_assignment](https://github.com/onyx-dot-app/onyx/tree/main/contributor_ip_assignment)) |

The strategic reading follows from the edition column. **For a self-hoster comparing free builds, PipesHub is the only one of the two that enforces source permissions.** That is the core promise of "secure enterprise search", and the strongest honest message PipesHub has.

The same strength is also its main exposure. The most damaging PipesHub bug class is permission sync that silently fails:
- Confluence pages with no explicit restriction got "zero permission edges", which hid them from every user ([#1189](https://github.com/pipeshub-ai/pipeshub-ai/issues/1189)).
- A SharePoint tenant with 200 sites and 400 users showed content to only one user ([#1549](https://github.com/pipeshub-ai/pipeshub-ai/issues/1549)).
- A Jira Data Center connector was missing from the chat picker for most of roughly 1,600 users ([#2701](https://github.com/pipeshub-ai/pipeshub-ai/issues/2701)).

A moat that leaks is worse than no moat, so this plan treats permission correctness as the first thing to prove.

## Users of both want the same three things and curse the same two

The two user bases ask for the same things. The positive comments about Onyx gather around three themes:
- **"Onyx actually finds things."** A support team searches Freshdesk, BookStack, Drive, YouTrack and Slack with citations ([HN 46049095](https://news.ycombinator.com/item?id=46049095)).
- **Self-hosting and data sovereignty.**
- **Permission mirroring.** Even Open WebUI's documentation credits it ([Open WebUI docs](https://docs.openwebui.com/alternatives/onyx)).

The PipesHub praise found was thinner and mostly opens bug reports. It is about the idea of an open-source Glean ("i love this idea to make opensource glean ai") and about Google Workspace working well ([#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573)). **No user was found praising PipesHub's knowledge graph by name.** That advantage is still vendor-asserted, which is why the plan includes a public benchmark.

Complaints match too:
- **Weight and fragility of self-hosting.** For Onyx, Vespa running out of memory on a 64 GB host ([#3427](https://github.com/onyx-dot-app/onyx/issues/3427)), and 2026 install breakages ([#9793](https://github.com/onyx-dot-app/onyx/issues/9793), [#9622](https://github.com/onyx-dot-app/onyx/issues/9622)). For PipesHub, about 36% of unaffiliated users' issues concern setup, including PDF indexing failing from lack of RAM ([#682](https://github.com/pipeshub-ai/pipeshub-ai/issues/682)).
- **Opaque connectors.** For Onyx, connectors "stuck in Deleting" and "no ETA for indexing" ([#1546](https://github.com/onyx-dot-app/onyx/issues/1546)). For PipesHub, about 24% of external issues concern connectors.

Onyx answered both complaint themes in 2026. It replaced Vespa with OpenSearch, added Lite, and rebuilt connector operations. PipesHub has not yet done the equivalent.

Two complaints are specific to PipesHub, and both damage trust:
- **Telemetry.** The self-hosted install enables data collection by default "without clear notice". One user then got a personalised outreach email. The issue had no maintainer reply after five weeks ([#3625](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625)). Related reports: no opt-out before the first push ([#3299](https://github.com/pipeshub-ai/pipeshub-ai/issues/3299)), and login events that carry the user's email ([#3302](https://github.com/pipeshub-ai/pipeshub-ai/issues/3302)).
- **Security inbox.** A security report emailed on 2 September got only an automatic acknowledgement, which broke the 48-hour commitment in SECURITY.md ([#3293](https://github.com/pipeshub-ai/pipeshub-ai/issues/3293)).

For a product sold on privacy and self-hosting, these are the cheapest and most urgent fixes in the plan.

Responsiveness is similar on paper. **37 of 84** unaffiliated PipesHub issues (44%) got no maintainer comment, with a median first response of 34.9 hours among those that did ([GitHub issues](https://github.com/pipeshub-ai/pipeshub-ai/issues)). For Onyx, 44% of open issues have no comments, and more of its issues closed as "not planned" (703) than as "completed" (648) ([Onyx issues](https://github.com/onyx-dot-app/onyx/issues)). Both projects route support to Discord, which hides it from evaluators.

Onyx's Launch HN also drew a warning PipesHub should take to heart. One critic called Onyx "clearly full of features ticked off a list that nobody has actually tried to use" ([HN 46046530](https://news.ycombinator.com/item?id=46046530), snippet). Matching Onyx feature for feature would invite the same verdict.

## The plan: four phases from trust repair to depth leadership

The plan is sequenced around one dependency chain. **Trust fixes come before the launch, because HN commenters will find telemetry and install breakage within hours. Consented telemetry comes before funnel metrics, because without it PipesHub cannot measure onboarding. Permission proof comes before enterprise claims and case studies.**

The plan also assumes PipesHub has far less capital than Onyx. The reported $250K pre-seed is unverified, against Onyx's $10M plus Zoom's backing. So the plan deliberately skips native mobile apps, deliberately skips chasing connector count, and adopts Onyx features only where user demand is on record.

Owner abbreviations:

| Code | Owner |
| --- | --- |
| FE | frontend |
| PY | backend Python |
| NODE | Node.js API |
| DEP | deployment |
| MKT | marketing |
| DREL | docs and developer relations |
| SEC | security and compliance |
| FND | founders |

Every success metric in the tables below is a **proposed target**, not a measured result.

### Phase 0 (days 0–30): repair trust and launch 1.0

The goal is to remove the self-inflicted trust failures that would sink a public launch, and then use the unused 1.0 moment.

| # | Deliverable | Gap and evidence | Owner | Success metric | Depends on |
| --- | --- | --- | --- | --- | --- |
| 0.1 | Explicit telemetry consent in the installer and in the first-run UI. Strip email from events. Publish a privacy note and reply on #3625. | [#3625](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625), [#3299](https://github.com/pipeshub-ai/pipeshub-ai/issues/3299), [#3302](https://github.com/pipeshub-ai/pipeshub-ai/issues/3302); Helm `telemetry.collection.enabled` defaults on ([values.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/values.yaml)) | DEP, NODE, FE | All three issues closed; 100% of new installs show a consent choice before the first event | — |
| 0.2 | A human-staffed security inbox: acknowledge within 48 hours, publish GitHub security advisories | [#3293](https://github.com/pipeshub-ai/pipeshub-ai/issues/3293), [SECURITY.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/SECURITY.md) | SEC, FND | 100% of reports acknowledged by a human within 48 hours | — |
| 0.3 | Patch-release lane (`release/1.0` branch, backports). Sync the Helm appVersion to the app version. | [#3030](https://github.com/pipeshub-ai/pipeshub-ai/issues/3030) fix sat unreleased; [Chart.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/Chart.yaml) says 0.10.0; Onyx patches three lines at once ([tags](https://github.com/onyx-dot-app/onyx/tags)) | DEP | A merged critical fix reaches a published patch in 72 hours or less; chart appVersion always equals the app version | The existing release gate |
| 0.4 | Docs-drift sweep. Generate the connector count from the registry, and fail CI on drift in README or locale files. | [#3779](https://github.com/pipeshub-ai/pipeshub-ai/issues/3779), [#3259](https://github.com/pipeshub-ai/pipeshub-ai/issues/3259), [#3016](https://github.com/pipeshub-ai/pipeshub-ai/issues/3016), [#2951](https://github.com/pipeshub-ai/pipeshub-ai/issues/2951); the count reads "30+", "40+" or "50+" depending on where you look | DREL | Zero open docs-drift issues; one connector number everywhere | — |
| 0.5 | Triage rotation for external issues | 44% of external issues unanswered; median first response 34.9 hours ([issues](https://github.com/pipeshub-ai/pipeshub-ai/issues)) | All engineering | 90% get a first response within 72 hours (baseline 31 of 84, 37%) | — |
| 0.6 | Fix or remove dead UX: the stubbed tour, the placeholder text on `/agents`, the hard-coded English survey, and default suggestion chips that name connectors the user may not have | [tours api.ts](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/components/tours/onboarding/api.ts), [agents/page.tsx](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/agents/page.tsx), [user-background-survey.tsx](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/components/surveys/user-background/user-background-survey.tsx) | FE | No placeholder strings reachable in the UI; the i18n check passes | — |
| 0.7 | Public editions matrix and pricing page that is honest about what EE gates (groups, SAML, organisation approval rules, delegation) | No PipesHub price found; Onyx sells at about $20 per user (unverified) ([aitoolsatlas](https://aitoolsatlas.ai/tools/onyx/pricing)) | FND, MKT | Page live; demo and trial requests tracked from it | Decision on 1.4 (OIDC in OSS) |
| 0.8 | Refresh the "PipesHub vs Onyx" page with current facts (Onyx is hybrid on OpenSearch). Lead with "permission sync and credential encryption are free". | Stale claim in [pipeshub-vs-onyx](https://pipeshub.com/pipeshub-vs-onyx) (snippet); Onyx gating in [Enterprise Edition](https://docs.onyx.app/admins/getting_started/enterprise_edition) | MKT | Page factually reviewed by engineering; linked from the README | — |
| 0.9 | 1.0 launch: Show HN, Product Hunt, a follow-up from heise, posts to r/selfhosted and r/LocalLLaMA, built around the Acme demo with permission-restricted answers | No HN or PH launch found; Onyx's Launch HN drew 254 points ([HN 46045987](https://news.ycombinator.com/item?id=46045987)) | MKT, FND | Launch month at 1,000 or more stars (baseline about 225 a month); Discord joins tracked | 0.1–0.4 shipped first |

### Phase 1 (days 30–90): a reliable first hour and a reliable first connector

The goal is to make install, onboarding and the first connector sync boringly reliable, and to give admins a reason to keep the product deployed.

| # | Deliverable | Gap and evidence | Owner | Success metric | Depends on |
| --- | --- | --- | --- | --- | --- |
| 1.1 | **Small profile** (8 GB or less, indexing on, hosted or tiny embeddings) plus a chat-and-MCP-only mode for evaluation | 16 GB host floor ([ADVANCED_DEPLOYMENT.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/ADVANCED_DEPLOYMENT.md)) against Onyx Lite at 2 GB ([Lite compose](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.onyx-lite.yml)); HN critics of Onyx's 10 GB floor | DEP, PY | A cited Acme answer on an 8 GB laptop within 15 minutes of running the curl command, tested in CI | 0.3 |
| 1.2 | Bundled TLS profile (Caddy or Traefik with Let's Encrypt) | README "white screen" warning ([README](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md)); Onyx bundles nginx with Let's Encrypt | DEP | A domain-based install serves HTTPS with zero manual steps | — |
| 1.3 | `pipeshub-cli` with install, upgrade, status, logs, offline mode and Windows support | [#3074](https://github.com/pipeshub-ai/pipeshub-ai/issues/3074); Onyx CLI and PowerShell installer ([onyx_cli.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/local/onyx_cli.mdx)) | DEP | The upgrade path passes the release gate on amd64 and arm64; Windows install documented | 0.3 |
| 1.4 | **Restore one generic OIDC provider in OSS**, keeping multi-provider SSO and SCIM in EE | 1.0 removed SAML and OIDC ([changelog/1.0.0.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/changelog/1.0.0.md)); Onyx CE allows one provider ([SSO providers](https://docs.onyx.app/deployment/authentication/sso_providers)) | NODE, FND | Okta, Entra and Keycloak tested in CI | A founder decision; this reverses a deliberate 1.0 choice |
| 1.5 | Onboarding rework: personal step, non-admin path, embedding/storage/SMTP defaults that can be deferred, and a final step to "connect your first source or explore the Acme demo" | Four infrastructure decisions before the first chat ([onboarding types.ts](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/onboarding/types.ts)); Onyx's in-chat flow ([constants.ts](https://github.com/onyx-dot-app/onyx/blob/main/web/src/lib/onboarding/constants.ts)) | FE, NODE | Share of consenting new orgs reaching a first cited answer in session one | 0.1 (consented telemetry) |
| 1.6 | **Permission-fidelity harness**: simulator-backed ACL tests for SharePoint, Confluence, Jira DC, Drive and Slack, run as a release-gate stage. Publish the resulting matrix. | [#1189](https://github.com/pipeshub-ai/pipeshub-ai/issues/1189), [#1549](https://github.com/pipeshub-ai/pipeshub-ai/issues/1549), [#1409](https://github.com/pipeshub-ai/pipeshub-ai/issues/1409), [#2701](https://github.com/pipeshub-ai/pipeshub-ai/issues/2701); simulators already exist ([CONNECTOR.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CONNECTOR.md)) | PY | 100% pass on every release; zero new permission-leak or invisible-record issues in the quarter | 0.3 |
| 1.7 | Connector preflight checks with scope-precise errors, completeness checks (counts at source against counts synced), failure banners, and per-record retry | Silent Notion stop at 40 of 1,175 pages ([#2994](https://github.com/pipeshub-ai/pipeshub-ai/issues/2994)); Onyx v5.0 connector checks and rebuilt status page ([Changelog](https://docs.onyx.app/changelog)) | PY, FE | Top 10 connectors covered; every incomplete sync is flagged as incomplete rather than reported as success | 1.6 |
| 1.8 | **Admin analytics v1 in OSS**: active users, queries, the feedback categories already captured, per-agent use, connector health | No analytics page ([en-US.json](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/lib/i18n/locales/en-US.json)); Onyx puts analytics on paid tiers ([Analytics](https://docs.onyx.app/admins/analytics/overview)) | FE, NODE, PY | Shipped; used weekly by 50% of active orgs that consented | 0.1 |
| 1.9 | Agents gallery at `/agents` with featured agents, templates and conversation starters | `/agents` redirects to chat; Onyx Explore Agents ([Custom Agents](https://docs.onyx.app/overview/core_features/agents)) | FE | Agents created per org; share of chats that run in an agent | — |
| 1.10 | Operator docs: sizing, Compose backup and restore, HA, monitoring, troubleshooting; docs CI | Backups documented for EKS only ([aws-eks.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/aws-eks.md)); Onyx has 35+ deployment pages ([docs](https://github.com/onyx-dot-app/onyx/tree/main/documentation/deployment)) | DREL, DEP | Every install flag and env var documented; docs CI green | 0.4 |
| 1.11 | Content funnel: alternatives hub (Glean, Onyx, Copilot, Guru, Open WebUI), a Glean cost and TCO calculator, a "Glean alternatives" listicle | One comparison page against Onyx's hub and calculator ([onyx.app/alternatives](https://onyx.app/alternatives), snippet) | MKT | Organic sessions; page rankings on "Glean alternative" queries | 0.7 |
| 1.12 | Ship the Discord bot (currently a stub) and run it in PipesHub's own Discord. Mirror solved threads to GitHub Discussions. | Stubbed bots ([bot-config-panel.tsx](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/app/(main)/workspace/bots/components/bot-config-panel.tsx)); Onyx runs its own bot in its community ([Discord bot](https://docs.onyx.app/overview/onyx_anywhere/discord_bot)) | NODE, DREL | Share of Discord questions answered by the bot with citations | — |

### Phase 2 (days 90–180): enterprise-ready and present where users work

The goal is to clear procurement gates for the cloud and for large self-hosted buyers, and to add the entry points that build daily habit.

| # | Deliverable | Gap and evidence | Owner | Success metric | Depends on |
| --- | --- | --- | --- | --- | --- |
| 2.1 | SOC 2 Type I for PipesHub Cloud, a public trust centre, and a subprocessor list | Onyx SOC 2 Type II ([Compliance](https://docs.onyx.app/security/onyx_cloud/compliance)); no confirmed PipesHub attestation (snippets conflict) | SEC, FND | Report issued; trust page live | 0.1, 0.2 |
| 2.2 | SIEM-exportable audit-log stream (EE) | Onyx v4.3 audit stream ([Audit Logs](https://docs.onyx.app/admins/auditing/audit_logs)); no PipesHub audit UI | NODE, PY | Events for authentication, access, configuration and credentials; Splunk and Sentinel formats | — |
| 2.3 | SCIM 2.0 and group sync (EE) | Onyx SCIM ([SCIM](https://docs.onyx.app/deployment/authentication/scim)); none found in PipesHub | NODE | Okta and Entra certified flows | 1.4 |
| 2.4 | White-labelling plus a **first-visit consent notice** and system announcements | Logo and name only ([en-US.json](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/frontend/lib/i18n/locales/en-US.json)); Onyx's options ([White Labeling](https://docs.onyx.app/admins/advanced_configs/white_labeling)) | FE | Consent notice in OSS; full rebrand in EE | — |
| 2.5 | Spending limits and per-user, per-model usage | Onyx v4.6 ([Spending Limits](https://docs.onyx.app/admins/advanced_configs/spending_limits)) | PY, NODE | Caps enforced at workspace, group and user level | 1.8 |
| 2.6 | Observability: Prometheus `/metrics` for every service, OpenTelemetry traces, Grafana dashboards in Helm, optional Sentry | Prometheus in 2 files in PipesHub against 25 in Onyx, plus Onyx dashboards ([dashboards](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx/dashboards)) | DEP, PY, NODE | Dashboards for indexing lag, query latency and governor memory pressure | — |
| 2.7 | Split the Helm chart into per-service Deployments (indexing, query, connectors, docling, Node) with HPA and KEDA | Monolith ([process_monitor.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/process_monitor.sh)) against per-worker scaling in Onyx | DEP, PY | The indexing tier scales on its own; memory per replica falls | 2.6 |
| 2.8 | Terraform module for AWS (then GCP and Azure), plus AWS Marketplace | Only an EKS guide ([deployment/](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/deployment)); Onyx covers three clouds ([modules](https://github.com/onyx-dot-app/onyx/tree/main/deployment/terraform/modules)) | DEP | A one-command AWS deploy; marketplace listing live | 2.7 |
| 2.9 | Chat parity where demand exists: Search mode (document list), incognito, user memory (wire up the existing `memory_tools`), per-chat filters, admin-controlled public share links | Onyx features ([Chat UI](https://docs.onyx.app/overview/core_features/chat)); memory exists unused in [agent_loop_lib](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend/python/app/agent_loop_lib) | FE, PY | Feature adoption; Search mode free (it is paid in Onyx) | — |
| 2.10 | **Chrome extension** (side panel, selection search, omnibox) | Onyx v2.9 ([Chrome extension](https://docs.onyx.app/overview/onyx_anywhere/chrome_extension)); none in PipesHub | FE, NODE | Weekly active users of the extension | MCP and OAuth already exist |
| 2.11 | **Microsoft Teams bot**, a lead neither product has | No Teams bot in either; PipesHub has strong Microsoft 365 connectors ([connector_factory.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/connectors/core/factory/connector_factory.py)) | NODE | Shipped with the same agent mapping as the Slack bot | Slack bot architecture |
| 2.12 | LLM gateway for Claude Code and Codex in OSS, reusing identity forwarding | Onyx gateway is Business+ ([LLM Gateway](https://docs.onyx.app/developers/guides/llm_gateway)); PipesHub has a `/v1/models` catalog | PY, NODE | Claude Code works through `ANTHROPIC_BASE_URL` with user attribution | — |
| 2.13 | i18n for French, Japanese, Portuguese, then Arabic with RTL; an axe accessibility audit | Onyx's nine languages, RTL and 211 accessibility fixes ([Changelog](https://docs.onyx.app/changelog)) | FE | New locales pass the i18n check; zero critical axe findings | — |

### Phase 3 (months 6–12): lead on depth and build proof

The goal is to turn PipesHub's engineering depth into evidence a buyer can verify, and to fill the gaps where demonstrated demand is unmet in both products.

| # | Deliverable | Gap and evidence | Owner | Success metric | Depends on |
| --- | --- | --- | --- | --- | --- |
| 3.1 | Scheduled and triggered agents, then the workflow builder behind `ENABLE_WORKFLOW_BUILDER` | Flag only ([featureflag.py](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/services/featureflag/featureflag.py)); Onyx Craft Scheduled Tasks ([Scheduled Tasks](https://docs.onyx.app/overview/core_features/craft_scheduled_tasks)) | PY, FE | Scheduled runs per org; tool approvals respected when no one is watching | 2.2 (audit trail) |
| 3.2 | Connectors aimed at demand Onyx has not met: Jira Service Management, Google Chat, Odoo (a contributor has offered to build it), email files, Snowflake registration | Onyx [#2281](https://github.com/onyx-dot-app/onyx/issues/2281) (177 comments, open), [#518](https://github.com/onyx-dot-app/onyx/issues/518) (open since 2023); PipesHub [#1870](https://github.com/pipeshub-ai/pipeshub-ai/issues/1870), [#588](https://github.com/pipeshub-ai/pipeshub-ai/issues/588), [#3752](https://github.com/pipeshub-ai/pipeshub-ai/issues/3752) | PY, community | Each one passes the permission harness from 1.6 | 1.6, 1.7 |
| 3.3 | Real-time sync for Slack, Confluence, Jira and GitHub (webhooks); a federated live-search option | Real-time today covers only Drive, Gmail, Box and Dropbox ([connectors/sources](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend/python/app/connectors/sources)); Onyx federated Slack ([Slack Federated](https://docs.onyx.app/admins/connectors/official/slack/slack_federated)) | PY | Median freshness under 5 minutes for those sources | 2.7 |
| 3.4 | **Public, reproducible retrieval benchmark** comparing permission-aware, KG-enhanced retrieval against a hybrid baseline. Decide on reranking with data. | The KG advantage is vendor-asserted only; a user asked for reranking ([#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573)); reranker removed (#5162) | PY, MKT | Benchmark published with harness; cited by third parties | `answer-quality-evals` CI |
| 3.5 | Named case studies from known scale deployments (roughly 1,600-user Jira DC; 400-user SharePoint) | No named PipesHub customers; Onyx logo wall ([Dealroom](https://dealroom.co/news/156435-zoom-buys-ai-search-startup-onyx-to-win-data-sovereignty-customers/)) | MKT, FND | 3 or more named case studies; G2 and Gartner reviews above 10 | 1.6, 2.1 |
| 3.6 | SOC 2 Type II; ISO 27001 if EU deals require it | Onyx Type II ([Compliance](https://docs.onyx.app/security/onyx_cloud/compliance)) | SEC | Report issued | 2.1 |
| 3.7 | Versioned schema migrations with down paths (fold in the in-flight Postgres document store) | 16 ad-hoc graph migrations; rollback "undoes code, not migrations" ([rollback.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/rollback.sh)); Onyx has 499 Alembic revisions | PY, NODE | Every release migrates and rolls back cleanly in CI | 0.3 |
| 3.8 | Embeddable website widget and a PWA-grade mobile web app (not native) | [#890](https://github.com/pipeshub-ai/pipeshub-ai/issues/890) mobile request; Onyx users treat mobile as a blocker ([#5618](https://github.com/onyx-dot-app/onyx/issues/5618)) | FE | Widget under 150 KB; Lighthouse PWA pass | 2.10 |
| 3.9 | Terraform provider for configuration as code (connectors, agents, models) | Onyx provider ([terraform-provider-onyx](https://github.com/onyx-dot-app/onyx/tree/main/terraform-provider-onyx)); PipesHub's OpenAPI and SDKs give it a head start | NODE, DEP | Published on the Terraform Registry | 2.8 |
| 3.10 | Contributor DX: devcontainer, pre-commit hooks, an external-contributor share above 25% | Onyx devcontainer and pre-commit ([.devcontainer](https://github.com/onyx-dot-app/onyx/tree/main/.devcontainer)); PipesHub merges come mostly from core accounts ([pulls](https://github.com/pipeshub-ai/pipeshub-ai/pulls)) | DREL | External share of merged PRs measured monthly | 0.5 |

### Scorecard across the year

| Metric | Baseline (1 Nov 2026) | 90 days | 12 months |
| --- | --- | --- | --- |
| GitHub stars and growth rate | 3,899; about 200–250 a month | 6,000; 500 a month | 15,000 |
| External issues answered within 72 hours | 37% (31 of 84) | 90% | 95% |
| Fix-to-patch release time for critical bugs | Unbounded (#3030) | 72 hours or less | 48 hours or less |
| Minimum RAM for a cited answer with indexing on | 16 GB host (8 GB eval) | 8 GB | 4 GB with hosted embeddings |
| Permission-harness pass rate across the top connectors | No harness | 100% on 5 connectors | 100% on 15 connectors |
| Compliance | None verified | Type I in progress | Type II issued |
| Named customers or case studies | 0 found | 1 | 3 or more |
| Enterprise SSO in OSS | Google and Microsoft only | Plus one generic OIDC provider | Unchanged; SCIM in EE |

## Conclusion

Comparing the two products feature by feature exaggerates Onyx's lead. Comparing free editions reverses it on the feature enterprise search is sold on: **in their free builds, only PipesHub enforces source permissions and encrypts credentials**. Onyx's real moat is everything around the engine. It runs on 2 GB, upgrades with one command, ships dashboards and audit streams, is reachable from a browser tab, has a SOC 2 report, and has 33,000 stars. All of that can be built, but none of it comes for free. PipesHub's sharpest risk is not a missing feature. It is that the permission sync it is uniquely positioned to win on fails silently, and that telemetry defaults and an unanswered security inbox contradict the privacy promise behind self-hosting.

So the order of the plan matters more than its length. The consent fix makes onboarding measurable. The patch lane makes the launch survivable. The permission harness makes case studies and SOC 2 claims credible. A Teams bot and a free Search mode let PipesHub lead in places Onyx has left open. The Zoom acquisition may make an independent, Apache-2.0 alternative more attractive to buyers who are wary of a communications vendor, but no evidence of that sentiment exists yet. Several inputs here rest on blocked sites and need checking once onyx.app, pipeshub.com, docs.pipeshub.com and Hacker News are reachable: Onyx's pricing, its ISO and HIPAA claims, its launch statistics, and PipesHub's own cloud attestations and funding. The decision on OIDC in OSS is the one that most needs founder judgement, because it reverses a deliberate 1.0 choice.
