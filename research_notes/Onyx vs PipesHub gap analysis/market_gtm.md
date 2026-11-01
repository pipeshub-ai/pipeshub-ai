# Onyx vs PipesHub: market positioning, GTM, pricing, funding, brand and community (as of 2026-11-01)

Research method note: this session's network sandbox blocked direct fetches of onyx.app, pipeshub.com, techcrunch.com, openalternative.co, everydev.ai and discord.com (DNS/proxy refusal). Facts below come from web-search result snippets (which quote those pages), the live GitHub repo page for Onyx (github.com fetch worked), and the GitHub API for PipesHub (repo-scoped access). Where a claim rests only on a search snippet or an aggregator, it is marked. To close the gaps, onyx.app, pipeshub.com, techcrunch.com and discord.com need to be allowed in the session's network settings.

## Positioning and messaging

### Takeaway
Both companies now use the same category language, "context layer" for teams and AI agents. Onyx also runs a large set of "alternative to X" pages, owns the "open-source Glean alternative" phrase, and says it is used by named enterprises. PipesHub pitches itself as an Apache-2.0, permission-aware, MCP- and agent-first context layer. Its "Glean alternative" message is carried mostly by its X handle name and one comparison page.

### Cited Findings
- Onyx's GitHub README headline is "The context layer powered by all your apps." It calls Onyx the knowledge and context layer for teams and AI agents, indexing 50+ apps, self-hostable, with web search, sandboxes, skills and agents on top of search. Repo description: "Open Source AI Platform - AI Chat with advanced features that works with every LLM" (fetched 2026-11-01) — [GitHub onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx)
- Onyx runs a "Glean alternative" landing page titled "The open-source Glean alternative at a fraction of the cost" — [onyx.app/alternatives/glean](https://onyx.app/alternatives/glean)
- Onyx has an alternatives hub at onyx.app/alternatives, including an Open WebUI page ("Enterprise search and knowledge management, beyond chat") — [onyx.app/alternatives](https://onyx.app/alternatives); [onyx.app/alternatives/open-webui](https://onyx.app/alternatives/open-webui)
- Onyx publishes an interactive "What does Glean actually cost?" calculator, a lead-generation and SEO asset — [onyx.app/glean-cost-calculator](https://onyx.app/glean-cost-calculator)
- Onyx publishes an "Open-Source Glean Alternatives for Enterprise Search (2026)" listicle on its own "Insights" blog, so it ranks for the competitor-alternatives query in which PipesHub is also a candidate — [onyx.app/insights/glean-alternatives](https://onyx.app/insights/glean-alternatives)
- Onyx's earlier 2025 positioning was "the first AI deep research agent for the workplace" plus open-source enterprise search (press release, March 2025) — [The AI Insider](https://theaiinsider.tech/?p=32672)
- PipesHub's GitHub description reads: "The open-source context layer for AI agents. PipesHub turns your company's knowledge (Slack, Drive, Jira, GitHub, Microsoft 365 and 40+ connectors) into a permission-aware workspace that agents can search, grep, navigate and cite. MCP, SDKs and built-in agents. Self-hosted." License Apache-2.0 (GitHub API, 2026-11-01) — [GitHub pipeshub-ai/pipeshub-ai](https://github.com/pipeshub-ai/pipeshub-ai)
- PipesHub's X handle display name is "PipesHub ( Open Source Alternative To Glean )" — [x.com/PipesHub](https://x.com/PipesHub?lang=en)
- PipesHub has a single comparison page, "PipesHub vs Glean: Best Open Source Alternative for Enterprise Search & AI Agents", which stresses Apache license, auditability, forking, self-hosting and horizontal scaling to "millions of documents" (search snippet; not independently benchmarked) — [pipeshub.com/pipeshub-vs-glean](https://pipeshub.com/pipeshub-vs-glean)
- The OpenAlternative listing positions PipesHub as an "Open Source Alternative to Glean, Microsoft 365 Copilot, and Guru" — [openalternative.co/pipeshub](https://openalternative.co/pipeshub)
- PipesHub runs an agent-facing docs entry point (docs.pipeshub.com/for-agents.md) aimed at Cursor/Claude users who adopt PipesHub as a context layer — [docs.pipeshub.com/for-agents.md](https://docs.pipeshub.com/for-agents.md)
- EveryDev.ai describes PipesHub as "a more extensible enterprise context layer than conventional workplace search or standalone RAG" (search snippet) — [everydev.ai/tools/pipeshub](https://www.everydev.ai/tools/pipeshub)

### Inferences
- The messaging has converged ("context layer for AI agents"), so PipesHub no longer stands apart on category name alone. It needs proof points: permission-aware grep/navigate, MCP, Apache-2.0 against Onyx's MIT CE plus proprietary EE.
- Onyx has turned "alternative to X" pages into a system (Glean, Open WebUI, and a hub of more). PipesHub has one known comparison page. That gap matters for bottom-of-funnel SEO.
- License contrast as a message: Onyx's Community Edition is MIT, but SSO/SCIM/RBAC/analytics/whitelabel sit in a separate Enterprise Edition (see Pricing). PipesHub ships Apache-2.0 and can claim "no open-core split" only if that is true of its feature set. That needs checking against the repo before anyone publishes the claim.

### Gaps
- Exact current homepage H1/taglines and persona sections for onyx.app and pipeshub.com could not be fetched (network blocked).
- Whether Onyx has "vs Microsoft Copilot", "vs Guru" or "vs Dashworks" pages specifically: the alternatives hub exists, but its full list was not retrievable.

## Pricing

### Takeaway
Onyx publishes self-serve SaaS pricing: Business at about $20/user/month billed annually ($25 monthly), a free self-hosted Community Edition, and Enterprise on contact-sales. It sells against Glean's estimated ~$55/user/month. I found no public PipesHub pricing.

### Cited Findings
- Onyx's own comparison pages list Business at $20/user/month annual ($25 monthly), as summarized by search; Glean is estimated at ~$55/user/month on Onyx's page — [onyx.app/alternatives/glean](https://onyx.app/alternatives/glean)
- An older price of $16/seat/month for "Onyx Cloud" appears in a July 2025 listing and is likely out of date — [Softwarefinder (Jul 25, 2025)](https://softwarefinder.com/artificial-intelligence/onyx-ai)
- Aggregators list Business from $20 and an Enterprise tier on "Contact sales". One unverified aggregator figure is ~$25+/user — [SaaSworthy](https://www.saasworthy.com/product/onyx-app/pricing); [aitoolsatlas](https://aitoolsatlas.ai/tools/onyx/pricing); [G2 pricing](https://www.g2.com/products/onyx-ai/pricing)
- Onyx documents billing in its docs (admins/billing/overview), so cloud self-serve billing is productized — [docs.onyx.app billing](https://docs.onyx.app/admins/billing/overview)
- Features gated in Onyx Enterprise Edition, per the README: team sharing of chats/agents; SSO (Google OAuth, OIDC, SAML) with SCIM; RBAC; usage analytics and query history; custom PII-removal/query-filter code; whitelabeling. The Community Edition (MIT) covers RAG, chat, agents and actions — [GitHub onyx-dot-app/onyx README](https://github.com/onyx-dot-app/onyx)
- PipesHub: searches for "PipesHub pricing" returned no PipesHub results. Techjockey has a listing, but no price was surfaced — [Techjockey PipesHub](https://www.techjockey.com/detail/pipeshub)

### Inferences
- Onyx has a low-friction path from free self-hosted to $20 self-serve cloud to sales-led Enterprise, plus a calculator that anchors buyers on Glean's price. Without a public price, PipesHub cannot capture self-serve or price-shopping traffic ("Glean pricing", "Glean cost" queries).
- Onyx gates the classic enterprise features (SSO/SCIM/RBAC/analytics) behind EE. If PipesHub includes these in Apache-2.0, that is a strong, quantifiable message against Onyx ("SSO is free").

### Gaps
- PipesHub's cloud/enterprise pricing, free tier and gating: not found publicly, and pipeshub.com was not fetchable.
- Onyx Enterprise list price and any seat minimums: not public.

## Funding and company

### Takeaway
Onyx (YC W24, founded 2023 by Yuhong Sun and Chris Weaver) raised a $10M seed in March 2025, co-led by Khosla Ventures and First Round Capital. I found no Onyx Series A. PipesHub is much earlier: a reported $250K pre-seed (June 2025) and a September 2026 report that it joined the AI2 Incubator, both from secondary sources.

### Cited Findings
- Onyx raised a $10M seed co-led by Khosla Ventures and First Round Capital, with Y Combinator and angels (press release republished 14 Mar 2025) — [The AI Insider](https://theaiinsider.tech/?p=32672); [Research Live](https://www.research-live.com/article/news/onyx-raises-10m-in-funding/id/5137073); [People Matters](https://sea.peoplemattersglobal.com/news/ai-and-emerging-tech/onyx-secures-10m-seed-funding-to-enhance-workplace-data-search-44837)
- TechCrunch covered the round and positioning on 2025-03-12: "Why Onyx thinks its open source solution will win enterprise search" (article not fetchable here) — [TechCrunch](https://www.techcrunch.com/2025/03/12/why-onyx-thinks-its-open-source-solution-will-win-enterprise-search/)
- YC's job page says Onyx "raised a $10M seed coming out of YCombinator, backed by Khosla Ventures" and First Round — [YC Onyx jobs](https://ycombinator.com/companies/onyx/jobs/qKqC8BX-fullstack-engineer)
- YC directory: founded 2023, batch W24, team size 13 (date of that figure not shown) — [YC Onyx](https://www.ycombinator.com/companies/onyx/jobs)
- Co-founders are Yuhong Sun and Chris Weaver — [Dealroom](https://app.dealroom.co/companies/onyx_8)
- Danswer's pre-YC funding: CB Insights shows $500K over 3 rounds, latest dated 2024-03-04 — [CB Insights Danswer](https://www.cbinsights.com/company/danswer/financials)
- One comparison site cites Glean at ~$750M raised against Onyx's $10M — summarized in search results for [neuronfeed Glean vs Onyx](https://neuronfeed.com/compare/glean-vs-onyx)
- Caution: "Onyx Series A/B" news in 2026 ($35M Series A led by Conviction; $113M Series B led by Bessemer at a $640M valuation) is about **Onyx Security**, an unrelated Israeli AI-agent security company. Do not attribute it to onyx.app — [Globes](https://en.globes.co.il/en/article-1001550885); [Dealroom news](https://dealroom.co/news/141979-onyx-raises-113m-series-b-at-640m-valuation-four-months-after-leaving-st/)
- PipesHub: a reported $250,000 pre-seed dated 2025-06-17, and a September 2026 item "PipesHub joins AI2 Incubator" (both from EveryDev.ai, via search snippet, unverified) — [EveryDev.ai PipesHub](https://www.everydev.ai/tools/pipeshub)
- PipesHub founding year conflicts: Techjockey says 2024, EveryDev.ai says launched 2025. The GitHub repo was created 2025-03-06 (GitHub API) — [Techjockey](https://www.techjockey.com/detail/pipeshub); [GitHub](https://github.com/pipeshub-ai/pipeshub-ai)

### Inferences
- Onyx has about 40x PipesHub's reported capital ($10M vs $0.25M, if the PipesHub figure is accurate). It also has YC, Khosla and First Round brand signals, which help with enterprise procurement trust and press access.
- No Onyx Series A appeared as of 2026-11-01 (about 20 months after the seed). That is surprising for a company of its traction. Either it is efficient or a raise is pending; this is unconfirmed.

### Gaps
- Current Onyx headcount and trend (LinkedIn not fetchable). The YC "13" figure is undated and probably stale.
- PipesHub founders, team size and investors, and confirmation of the AI2 Incubator news, are not verified from a primary source.

## Customers

### Takeaway
Onyx names marquee logos (Netflix, Ramp, Thales Group, Bitwarden) and has a Ramp reliability testimonial. I found no named PipesHub customers in public sources.

### Cited Findings
- The Onyx seed press release says it is "adopted by companies like Netflix, Thales Group, and Bitwarden" (Mar 2025) — [The AI Insider](https://theaiinsider.tech/?p=32672)
- The Onyx site says Ramp chose Onyx over competitors for answer reliability (via search summary). PremAI's 2026 listicle repeats Netflix, Ramp and Thales as production users. No primary confirmation from Netflix or Ramp was found — [onyx.app/alternatives/glean](https://onyx.app/alternatives/glean); [PremAI blog](https://www.premai.io/blog/12-best-glean-alternatives-for-private-enterprise-ai-search-2026/)
- A Gartner Peer Insights product page exists for PipesHub (review count not retrieved) — [Gartner PipesHub](https://www.gartner.com/reviews/product/pipeshub-498905425)

### Inferences
- Logos are the clearest trust gap. Onyx's Fortune-500-style names show up in third-party listicles, so they compound in SEO and in AI-answer engines. PipesHub has nothing equivalent in the public record.

### Gaps
- Onyx claimed user or deployment counts, and its case-study pages: not retrievable.
- PipesHub customers, logos and deployment counts: none found.

## Content and community engine

### Takeaway
Onyx's community footprint is about 8.5x PipesHub's on GitHub stars (33.1k vs 3.9k). It is still growing by roughly 900+ stars a month. Onyx pairs this with an "Insights" SEO blog, alternatives pages, a cost calculator and a Discord. PipesHub has Discord, X, LinkedIn and a YouTube demo, but its comparison and SEO content is thin by comparison.

### Cited Findings
- Onyx GitHub (fetched 2026-11-01): 33.1k stars, 4.7k forks, 153 watchers, 11,379 commits, Discord at discord.gg/TDJ59cGV2X — [GitHub onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx)
- Onyx star history: about 27k stars with global rank #1242 as of 2026-04-15 (Star History); Trendshift lists 29.9k; older snapshots list 19.5k and 18k+ — [star-history.com](https://star-history.com/onyx-dot-app/onyx); [Trendshift](https://trendshift.io/repositories/12516); [OSSInsight](https://ossinsight.io/analyze/onyx-dot-app/onyx)
- PipesHub GitHub (GitHub API, 2026-11-01): 3,899 stars, 631 forks, 17 watchers, 204 open issues, about 83 contributors (contributors pagination), repo created 2025-03-06 — [GitHub pipeshub-ai/pipeshub-ai](https://github.com/pipeshub-ai/pipeshub-ai)
- PipesHub release cadence: v0.8.0 (2026-09-14), v0.9.0 (09-28), v0.9.1 (09-28), v0.10.0 (10-12), v1.0.0 (2026-10-27), roughly every two weeks — [PipesHub releases](https://github.com/pipeshub-ai/pipeshub-ai/releases)
- PipesHub community channels in its README: Discord (discord.com/invite/K5RskzJBm2), X @PipesHub, LinkedIn company/pipeshub, a YouTube demo video (youtu.be/VgyND38QZ7w) — [PipesHub README](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md)
- Onyx has a self-hosted LLM/billing/security documentation site that doubles as marketing (security architecture FAQ, compliance pages) — [docs.onyx.app security FAQ](https://docs.onyx.app/security/architecture/faq)
- Third-party listicles mostly rank Onyx as the strongest open-source Glean alternative and PipesHub as an "emerging contender" — [neuralchainai](https://neuralchainai.com/blog/top-open-source-glean-alternatives-enterprise-search-ai/); [openalternative Glean alternatives](https://openalternative.co/alternatives/glean)
- PipesHub third-party coverage is mostly directory listings (AlternativeTo, EveryDev, Techjockey, OnWorks) and a July 2026 BrightCoding blog post — [AlternativeTo](https://alternativeto.net/software/pipeshub/about/); [BrightCoding](https://www.blog.brightcoding.dev/2026/07/03/pipeshub-the-revolutionary-workplace-ai-platform-transforming-enterprise-search)

### Inferences
- From 27k (2026-04-15) to 33.1k (2026-11-01), Onyx gained about 6.1k stars in about 6.5 months, around 940 a month. That is roughly a quarter of PipesHub's lifetime total every month. (Derived; the Star History and GitHub figures come from different tools.)
- Watchers are the stronger engagement signal: 153 vs 17 (about 9x). Forks are 4.7k vs 631 (about 7.5x).
- Onyx's "Insights" blog plus alternatives pages and calculator form a complete SEO funnel: listicle, then comparison, then cost tool, then pricing. PipesHub's visible equivalent is one vs-Glean page.

### Gaps
- Discord member counts for both: the Discord API was blocked by the proxy.
- X and LinkedIn follower counts for both: not retrievable.
- Blog post cadence for both, and YouTube, webinar and newsletter presence for Onyx: not retrievable.

## Launch and distribution

### Takeaway
Onyx has a multi-launch track record: Launch HN in February 2024 (231 points), Show HN for Deep Research (125 points), Product Hunt in September 2025, and TechCrunch coverage. For PipesHub I found no HN, Product Hunt or tier-1 press launch.

### Cited Findings
- "Launch HN: Danswer (YC W24) – Open-source AI search and chat over private data", 2024-02-22, 231 points, 129 comments — [HN item 39467413](https://news.ycombinator.com/item?id=39467413)
- "Show HN: Open-source Deep Research across workplace applications", about 125 points (early 2025) — [HN mirror 43242551](https://hn.svelte.dev/item/43242551)
- Onyx on Product Hunt: launched 2025-09-23 with 159 upvotes, 28 comments and #16 for the day (per an aggregator, unverified on Product Hunt directly) — [hunted.space](https://www.hunted.space/product/onyx-8/launches/onyx-10)
- Onyx is listed on G2 (pricing and AI marketplace), Capterra and GetApp — [G2](https://www.g2.com/products/onyx-ai/pricing); [Capterra](https://www.capterra.com/p/10037808/Onyx-AI/); [GetApp UK](https://www.getapp.co.uk/software/2152755/onyx-ai)
- PipesHub is listed on Gartner Peer Insights, AlternativeTo, OpenAlternative and an MCP directory (prismix.dev) — [Gartner](https://www.gartner.com/reviews/product/pipeshub-498905425); [AlternativeTo](https://alternativeto.net/software/pipeshub); [prismix MCP](https://prismix.dev/mcp/pipeshub-ai-pipeshub-ai)
- A Product Hunt search for PipesHub found no listing — [search results](https://www.hunted.space/product/workplace-ai)

### Inferences
- PipesHub's v1.0.0 release (2026-10-27) is a natural, unused launch moment for Launch/Show HN, Product Hunt, press, and an "open-source Glean alternative hits 1.0" story.

### Gaps
- AWS, Azure or GCP Marketplace listings for either: not found or verified.
- Analyst mentions (Gartner/Forrester) and awards for either: not found.

## Trust signals

### Takeaway
Onyx publicly claims SOC 2 Type II and GDPR in its docs, and a trust-center listing adds ISO 27001 and HIPAA. I found no public compliance attestation for PipesHub.

### Cited Findings
- Onyx docs state Onyx is SOC 2 Type II and GDPR compliant — [docs.onyx.app compliance](https://docs.onyx.app/security/onyx_cloud/compliance); [docs.onyx.app security FAQ](https://docs.onyx.app/security/architecture/faq)
- A trust-list aggregator lists Onyx's trust center with SOC 2 Type II, ISO 27001, HIPAA and GDPR (aggregator, unverified) — [trustlists.org](https://trustlists.org/company/onyx/)
- Caution: "Aderant achieves SOC 2 Type 2 for Onyx" (March 2026) is about Aderant's legal product named Onyx, not onyx.app — [Aderant](https://www.aderant.com/news-pr/aderant-achieves-soc-2-type-2-examination-for-onyx/)
- PipesHub has a SECURITY.md (vulnerability disclosure) in its repo. No SOC 2, ISO or HIPAA claim was found — [PipesHub repo](https://github.com/pipeshub-ai/pipeshub-ai)

### Inferences
- For a cloud or enterprise sale, Onyx's SOC 2 Type II gets past procurement gates that PipesHub cannot clear today unless it sells self-hosted only. A trust center (for example Vanta or Drata) is a prerequisite for PipesHub's cloud GTM.

### Gaps
- Whether PipesHub has an audit in progress or a trust page on pipeshub.com: not retrievable.

## Competitive landscape

### Takeaway
Both companies frame Glean as the main enemy. Onyx compares on cost (about $20 vs $55/user), self-hosting and air-gapped deployment, and admits fewer connectors (40+ vs Glean's 100+). PipesHub compares on Apache-2.0 openness and frames itself against Glean, Microsoft 365 Copilot and Guru. I found no public head-to-head page between Onyx and PipesHub, although Onyx's Glean-alternatives listicle sits in the same search results.

### Cited Findings
- Onyx's Glean page: Onyx is open-source, self-hostable and air-gapped capable; Glean is not. It lists Onyx at 40+ connectors against Glean's 100+ (per search summary) — [onyx.app/alternatives/glean](https://onyx.app/alternatives/glean)
- Onyx targets Open WebUI as a chat-only alternative ("beyond chat") — [onyx.app/alternatives/open-webui](https://onyx.app/alternatives/open-webui)
- PipesHub frames itself as an alternative to Glean, Microsoft 365 Copilot and Guru — [openalternative.co/pipeshub](https://openalternative.co/pipeshub); [pipeshub.com/pipeshub-vs-glean](https://pipeshub.com/pipeshub-vs-glean)
- Independent listicles name Onyx "the most direct open-source equivalent to Glean". One calls PipesHub an "emerging" contender — [neuralchainai](https://neuralchainai.com/blog/top-open-source-glean-alternatives-enterprise-search-ai/); [Atlan](https://atlan.com/know/ai-agent/data-for-ai/glean-alternatives/); [nexos.ai](https://nexos.ai/blog/glean-alternatives/)
- Reviewers flag that self-hosting moves cost to operations ("removing the license line adds an operations line"), a counter-argument that applies to both — [cloudspress](https://www.cloudspress.com/best-glean-alternatives-for-companies-that-want-to-avoid-vendor-lock-in/)

### Inferences
- The connector count is now a point where PipesHub can attack. PipesHub claims 40+ (repo description) and up to 50+ (docs snippet); Onyx claims 50+ in its README and 40+ on its Glean page. Neither leads clearly, so depth claims (permission sync, grep/navigate, MCP) separate the two better than counts.
- Missing PipesHub content assets, ranked by how much search intent Onyx already captures: (1) "Glean alternatives" listicle, (2) "PipesHub vs Onyx" page, (3) Glean cost or ROI calculator, (4) vs Microsoft Copilot / Guru / Dashworks pages, (5) public pricing page.

### Gaps
- Onyx pages comparing against Microsoft Copilot, Guru or Dashworks, and any Onyx page mentioning PipesHub: not verified.
- Dashworks is not covered in any source retrieved here.
