# User sentiment about Onyx (onyx.app, onyx-dot-app/onyx, formerly Danswer)

Research date: 2026-11-01. Method note: in this session WebFetch could not reach news.ycombinator.com, hn.algolia.com, onyx.app, techcrunch.com, reddit.com, g2.com or producthunt.com (the hosts are outside the allowed network domains). HN, Product Hunt and vendor material below comes from WebSearch result summaries and snippets, so quotes are as short as the snippets. GitHub data comes from the GitHub search API through `gh` (the repo endpoints were blocked, search was not). Reddit, G2 and Capterra turned up almost nothing usable (see Gaps). Theme frequency estimates are flagged as estimates.

## 1. What users appreciate most (features praised by name)

### Takeaway
Users praise three things most: (a) one search across many connected tools, with citations ("Onyx actually finds things"); (b) self-hosting, air-gapped deployment and an MIT core, for data sovereignty; and (c) permission-aware retrieval and the Slack bot. Praise is consistent but sparse. Most of it comes from HN threads and comparison blogs, not review sites.

### Cited Findings
- **Unified search plus citations (the most-praised trait in HN discussion).** A long-time user (HN user bks, Launch HN Onyx thread, ~Nov 2025) wrote that they had "been using Onyx (and Danswer before it) for over a year." Their support team relies on it daily across Freshdesk, Bookstack, Google Drive, YouTrack and Slack. They pushed back on critics because "Onyx actually finds things", and they called the value unified search across systems with citations. — [HN comment 46049095](https://news.ycombinator.com/item?id=46049095)
- **People switching to Onyx from Open WebUI.** One HN commenter (throw4039, ~Nov 2025): "Looks awesome! I'm really trying to switch off Open WebUI due to its general slowness and bugginess, as well as documentation which is almost entirely slop." They also noted that Onyx lacked MinerU as a PDF backend. — [HN comment 46052745](https://news.ycombinator.com/item?id=46052745)
- **Self-hosting and data sovereignty is the main reason to choose it.** A 2026 review argues that self-hosting is the main reason to pick Onyx (data sovereignty, air-gapped operation). It cites an MIT license, 51 connectors, and Onyx Cloud at $20/user/month. — [teamazing review 2026](https://www.teamazing.com/blog/onyx-ai-enterprise-review-2026/)
- **Air-gapped design (vendor docs).** The docs describe "an air-gappable, self-hosted deployment where the document index, database, and processing all happen within a self-contained set of services." — [Onyx Slack Bot docs](https://docs.onyx.app/overview/onyx_anywhere/slack)
- **Permission mirroring (praised even by a competitor).** Open WebUI's own docs say "Onyx excels at enterprise search and connecting AI to your existing tools. Open WebUI excels as a general AI platform." They credit Onyx's "40+ connectors with automatic syncing and permission-aware retrieval." — [Open WebUI docs: Onyx](https://docs.openwebui.com/alternatives/onyx.md)
- **Connector permission modes.** A connector can be private, public, or set to "Auto Sync Permissions". Only the last mode carries over the source's ACLs. — [WZ-IT install guide](https://wz-it.com/en/blog/onyx-self-hosted-installation/)
- **Slack bot (DanswerBot, praised since 2023).** The Show HN pitched it as an open-source Slack bot to automate repetitive questions. Its answers are backed by quotes, with all source documents linked so askers can check them. — [Show HN: DanswerBot (Sept 2023)](https://news.ycombinator.com/item?id=37677473). The maker on Product Hunt said: "The number one use case for Slack is answering questions! E.g., you can configure the bot to answer in #eng-newhires and onboarding employees can self-serve verified, well-documented answers." — [Product Hunt: Onyx](https://www.producthunt.com/products/onyx-8)
- **Projects (NotebookLM-style).** An HN user asked whether Onyx is a self-hosted NotebookLM. The maintainer said Projects works similarly: you upload many files beyond the model's context limit and ask questions about them. This is a vendor reply. — [HN threads (Axsuul)](https://news.ycombinator.com/threads?id=Axsuul)
- **Multilingual retrieval (founder claim, Feb 2024).** A founder said they were "the only project (open source or closed), as far as I know, that handles multilingual quite well." — [Launch HN: Danswer (YC W24)](https://news.ycombinator.com/item?id=39467413)
- **Product Hunt praise for the team.** A commenter called the team "super talented & responsive." — [Product Hunt: Onyx](https://www.producthunt.com/products/onyx-8)
- **One review-aggregator user** praised search efficiency for internal information, but said they still do extra searches in other AI tools when Onyx misses on technical questions. — [softwarefinder Onyx reviews](https://softwarefinder.com/artificial-intelligence/onyx-ai/reviews)
- **"Works with any LLM" positioning.** The founders' Launch HN describes "an open-source chat that works with any LLM (proprietary + open weight)" with "RAG, web search, MCP, deep research, memory." — [Launch HN: Onyx (YC W24)](https://news.ycombinator.com/item?id=46045987)
- **Mobile UI is the main gap for users who like it.** In GitHub issue #5618 (2025-10-06): "So far I really like Onyx, but it's not usable as an ai provider during meetings if it doesn't work on mobile." The user names Open WebUI's iOS UI as the benchmark. — [GitHub #5618](https://github.com/onyx-dot-app/onyx/issues/5618)

### Inferences
- Estimated theme frequency among the positive comments found: unified search, connectors and citations about 40%; self-hosting, privacy and MIT license about 30%; permission sync about 15%; Slack bot about 10%; team responsiveness about 5%. These are rough shares of roughly 15 to 20 positive data points, not a statistically meaningful sample.
- Users praise the knowledge-retrieval layer (connectors, permissions, citations) far more than the chat UX. The chat UX is where Open WebUI and LibreChat are seen as stronger.

### Gaps
- No G2, Capterra or Gartner Peer Insights reviews of substance were found. One blog says the G2 listing shows zero reviews, and aggregators show 1 or 2. — [teamazing](https://www.teamazing.com/blog/onyx-ai-enterprise-review-2026/), [softwarefinder](https://softwarefinder.com/artificial-intelligence/onyx-ai/reviews)
- No Reddit threads surfaced in four different searches, and reddit.com could not be fetched directly.

## 2. Most common complaints, bugs, missing features and feature requests

### Takeaway
The complaints cluster into five themes: (1) heavy, fragile self-hosting (Vespa out-of-memory kills, a 4 vCPU / 10 GB RAM minimum, broken Docker, Kubernetes and install.sh paths); (2) slow, opaque indexing and stuck connectors; (3) LLM-provider friction (2024 issues: it kept asking for an OpenAI key under Ollama; 2026 issues: LiteLLM and OpenAI-compatible capability flags); (4) connector coverage requests, the largest single category; and (5) "checklist" features that feel unpolished, plus open-core licensing. Onyx later replaced Vespa with OpenSearch, which addresses the top resource complaint.

### Cited Findings
**Top GitHub issues by reactions (gh search API, pulled 2026-11-01):**
- #4539 "MCP support" (2025-04-16): 28 reactions, 14 comments, closed. This is the most-upvoted issue ever. — [GitHub #4539](https://github.com/onyx-dot-app/onyx/issues/4539)
- #4025 "github connector should index source code" (2025-02): 18 reactions. #4717 "Gitlab Connector for the code base itself" (2025-05): 10 reactions. — [#4025](https://github.com/onyx-dot-app/onyx/issues/4025), [#4717](https://github.com/onyx-dot-app/onyx/issues/4717)
- #1261 NextCloud integration (17 reactions); #156 Microsoft Teams connector (15); #4716 LDAP auth and grouping (14); #924 S3 connector (12); #1030 "How to access Danswer through an API" (12 reactions, 13 comments); #1165 "work with other Vector Database" (11); #1197 "Requires openai api key even though other provider selected" (10); #971 Helm charts (9 reactions, 26 comments); #4805 voice support (9); #518 Google Chat (9 reactions, still open since 2023-10). — [GitHub search results](https://github.com/onyx-dot-app/onyx/issues?q=is%3Aissue+sort%3Areactions-%2B1-desc)
- #4786 "PDF Answers Should Link to Exact Pages and Multiple Sources" (2025-05, 8 reactions). #10262 "Shared/Team Projects" (2026-04, 8 reactions): "Projects (`UserProject`) are scoped to individual users… cannot be shared with others." — [#4786](https://github.com/onyx-dot-app/onyx/issues/4786), [#10262](https://github.com/onyx-dot-app/onyx/issues/10262)
- 2026 top requests are mostly about LLM gateways and self-hosted models: #11142, a custom base URL for STT/TTS (8 reactions, open); #7277, LiteLLM as a provider (7); #10085, "no way to manually override model capability flags (e.g. vision)" for OpenAI-compatible providers (6); #10461, LiteLLM `user` / `chat_session_id` dropped (5, open); #10787, M365 Mail/Outlook connector (7). — [#11142](https://github.com/onyx-dot-app/onyx/issues/11142), [#10085](https://github.com/onyx-dot-app/onyx/issues/10085), [#10461](https://github.com/onyx-dot-app/onyx/issues/10461)

**Most-commented issues:**
- #2281 "Jira Service Management Connector" (2024-08): 177 comments, still open. This is by far the most-commented issue. — [GitHub #2281](https://github.com/onyx-dot-app/onyx/issues/2281)
- #1414 "Danswer asks for an OpenAI API Key even with Ollama configuration" (2024-05-02, 26 comments): "The service still continues to ask fo[r]…" an OpenAI key after configuring Ollama per the docs. Related: #940 and #1148 "Ollama Network Error" (13 comments each). — [GitHub #1414](https://github.com/onyx-dot-app/onyx/issues/1414)
- #3427 "High memory consumption" (2024-12-11, 19 comments): "The host have 64GB of RAM, but vespa keeps getting killed for OOM. And then while it is restarted, the backend cannot reach again for vespa." — [GitHub #3427](https://github.com/onyx-dot-app/onyx/issues/3427)
- #1378 "Connectors Remain Stuck in 'Deleting' State" (2024-04-25, 17 comments): "they became stuck in the 'Deleting' state. Subsequently, all indexing processes began to fail." — [GitHub #1378](https://github.com/onyx-dot-app/onyx/issues/1378)
- #1546 "No ETA for indexing, too slow, can't parallel index" (2024-05-31, 15 comments): "it's at 80k 'documents' by now and no end in sight… over a day so far… about 1 slack 'document per second'… Indexing of slack blocks all other indexing." — [GitHub #1546](https://github.com/onyx-dot-app/onyx/issues/1546)
- #7378 "Postgres installation is failing" on Kubernetes per the docs (2026-01-13, 16 comments): api-server in CrashLoopBackOff. — [GitHub #7378](https://github.com/onyx-dot-app/onyx/issues/7378)
- #9793 (2026-03-31): "docker compose up -d fails: minio not started but FILE_STORE_BACKEND defaults to s3", following the official docs. — [GitHub #9793](https://github.com/onyx-dot-app/onyx/issues/9793)
- #9622 (2026-03-25): "install.sh exits silently when user provides any non-default input to interactive prompts" (a `set -euo pipefail` bug). — [GitHub #9622](https://github.com/onyx-dot-app/onyx/issues/9622)
- #9015 "Deep Research failed to generate any research tasks" (2026-03, open). #7117 "Error on latest version v2.6.2" (2025-12-30). — [#9015](https://github.com/onyx-dot-app/onyx/issues/9015), [#7117](https://github.com/onyx-dot-app/onyx/issues/7117)

**Hacker News criticism (Launch HN: Onyx, ~Nov 2025, 254 points and 160 comments):**
- rao-v: "I was pretty excited for Onyx as a way to stand up a useful open source RAG + LL[M]…". They said that on their last try it was "clearly full of features ticked off a list that nobody has actually tried to use", and that indexed documents were hard to track and map back to sources. The team replied that the admin side needed work and promised a large overhaul. — [HN 46046530](https://news.ycombinator.com/item?id=46046530)
- Setup weight: one user said the Docker install "seems to assume you need to scale to 100s+ users." The team replied that "pretty much all of them are necessary to run locally." Another asked: "Its requirement is 4 vCPU and 10GB RAM?" — [Launch HN: Onyx](https://news.ycombinator.com/item?id=46045987)
- Open-core licensing: "as long as you have Pricing on your website your product is not open source in the true spirit of open sourceness". Another commenter: "there are parts of the open source codebase that require an enterprise license to use." The maintainers replied that chat stays open source and that they moved SSO from enterprise-only to MIT. — [HN 46062910](https://news.ycombinator.com/item?id=46062910), [Launch HN: Onyx](https://news.ycombinator.com/item?id=46045987)
- On chat UIs in general: "Nobody came up with a UI that has decent search experience." — [WZ-IT summary of HN](https://wz-it.com/en/blog/onyx-self-hosted-installation/) (via search summary)
- 2023 Show HN: a commenter noted it only worked with OpenAI at the time. The team said open-model support would come later. — [Show HN: Danswer (Jul 2023)](https://news.ycombinator.com/item?id=36667374)

**Third-party write-ups on weight and editions:**
- Stack: a vector plus keyword index, background workers, model servers, Redis and MinIO. The cited sizing is at least 4 vCPU and 10 GB RAM. — [amaiko vs Onyx](https://amaiko.ai/compare/amaiko-vs-onyx) (competitor page)
- Vespa "consuming 42 GB on a 64 GB machine". "Budget 4-8 GB just for the index container". By v4.9.0 (Oct 2026), Docker Compose ships OpenSearch 3.6.0 and no Vespa. Vespa is used only in versions up to 3. "Onyx Lite" runs on 2 vCPU / 2 GB but does not index documents. — [DejaViewed guide (Oct 2026)](https://dejaviewed.dev/guides/onyx-self-hosted-ai)
- Vespa's own case study says Onyx moved from self-hosted Vespa to Vespa Cloud for its managed offering (vendor marketing). — [Vespa.ai: Onyx](https://vespa.ai/onyx/)
- Paid-tier gating: user groups, RBAC and SSO are paid features for self-hosted deployments, and `ee` directories use a commercial license. — [wz-it comparison 2026](https://wz-it.com/en/blog/open-source-ai-chat-platforms-comparison/), [Glean alternatives roundups](https://slite.com/learn/glean-alternatives)
- An aggregator summary cites "occasional indexing issues and bugs." — [softwarefinder](https://softwarefinder.com/artificial-intelligence/onyx-ai)

**Issue-title counts (gh search, a proxy for frequency):** "connector" appears in 265 issue titles; "slack" in 53; "ollama" in 28; "vespa" anywhere in 84 issues; "permission" in 8 titles; "license" in 4. — [GitHub search](https://github.com/onyx-dot-app/onyx/issues)

### Inferences
- Estimated frequency of complaint themes, from issue counts plus qualitative sources:
  - Connector gaps and connector bugs: the largest theme, roughly 20 to 25% of issue titles.
  - Self-hosting weight and deploy breakage (Vespa, Docker, Helm, Kubernetes, install.sh): roughly 15 to 20% of discussion. This is also the dominant HN complaint.
  - LLM-provider configuration (Ollama in 2024, LiteLLM and OpenAI-compatible in 2026): about 10%.
  - Indexing speed, progress visibility and stuck connectors: about 10%.
  - UX polish, mobile and admin: about 5 to 10%.
  - Open-core licensing objections: about 5%, but loud on HN.
- Requests have moved from "basic connectors and local LLMs" (2023 to 2024) to "MCP, code-repo indexing, team sharing, LLM gateways and self-hosted multimodal" (2025 to 2026).
- The Vespa-to-OpenSearch move (v4.x, 2026) likely answers the resource complaints in #3427 and the HN threads. No source states Onyx's stated reason for it.

### Gaps
- Issue comment threads could not be read (repo endpoints were blocked), so the bodies of 177-comment threads like #2281 are not quoted. They are likely mostly "+1"s.
- GitHub Discussions could not be queried (GraphQL was blocked).

## 3. Community (Slack/Discord) and maintainer responsiveness

### Takeaway
There are few direct testimonials about responsiveness, but the ones found are positive ("super talented & responsive"), and founders reply visibly on HN. Tracker data show a fast-moving project: 167 open issues against about 1,523 total, and very high PR volume. But more issues were closed "not planned" than "completed", and 44% of open issues have no comments.

### Cited Findings
- Support channels: the docs point users to the community Discord (#support, where users can tag @OnyxBot) and a public Slack with #ask-onyx, where "the Slack bot is live and serving answers to the community." — [Onyx Discord bot docs](https://docs.onyx.app/overview/onyx_anywhere/discord_bot), [Onyx Slack bot docs](https://danswer.mintlify.app/overview/onyx_anywhere/slack)
- Product Hunt commenter: the team is "super talented & responsive." — [Product Hunt](https://www.producthunt.com/products/onyx-8)
- Founders engaged with critics on HN. On admin UX, they promised a "large overhaul". On licensing, they moved SSO to MIT. — [Launch HN: Onyx](https://news.ycombinator.com/item?id=46045987)
- An example of a declined request: a Slack thread-summary feature request (#1107) was closed as not planned. — [GitHub #1107](https://github.com/onyx-dot-app/onyx/issues/1107)
- Tracker stats (gh search, 2026-11-01):
  - 167 open issues out of 1,523 total.
  - 73 open issues (44%) have 0 comments.
  - Only 25 open issues predate 2026-05.
  - Closed issues: 703 "not planned" vs 648 "completed".
  - Issues filed per year: 2023: 161; 2024: 426; 2025: 389; 2026 to date: 547.
  - About 12,138 merged PRs in total, and about 9,100 PRs opened in 2026 alone.
  - Only 6 issues carry a "bug" label.
  — [GitHub issues](https://github.com/onyx-dot-app/onyx/issues)
- Glassdoor (4 reviews) describes a lean startup where "you will need to put in the hours." — [Glassdoor](https://www.glassdoor.com/Reviews/Onyx-AI-Reviews-E10693311.htm)

### Inferences
- The low count of old open issues plus the large "not planned" count suggests aggressive triage or a stale-bot. That keeps the backlog small but may frustrate requesters, for example the 177-comment JSM connector request still open since 2024-08.
- About 9,100 PRs in 2026 implies heavy automation (bots or AI-assisted PRs) or a much larger team after the funding and Zoom deal. This was not verified.

### Gaps
- No independent first-hand accounts of Discord or Slack response times were found. Access to the community workspaces was not attempted (login required).

## 4. Customer case studies and testimonials Onyx publishes

### Takeaway
Onyx's public proof points are logos and scale claims rather than detailed case studies. The logos are Netflix, Ramp, Thales, Bitwarden, Roku, Applied Intuition, NASA and UC San Diego. The scale claims are "dozens of enterprise customers" (Mar 2025) and "over 1 million queries per week" (Sept 2026). No verbatim customer quotes were found.

### Cited Findings
- Netflix, Ramp and Thales Group are named most often (Mar 2025 funding coverage). The company said it works with "dozens of enterprise customers." Co-founder Yuhong Sun: "Ramp was actually one of the early teams that found us." — [TechCrunch via Yahoo (2025-03-12)](https://uk.finance.yahoo.com/news/why-onyx-thinks-open-source-150000011.html), [American Bazaar](https://americanbazaaronline.com/2025/03/13/onyx-secures-10-million-from-khosla-ventures-to-redefine-ai-enterprise-search-460691/)
- G2 lists Bitwarden, Roku and Applied Intuition. Applied Intuition was found only in that G2 listing. — [G2 discuss page](https://www.g2.com/products/onyx-ai/discuss)
- Zoom acquisition coverage: over 1 million queries per week; customers include NASA, Ramp and UC San Diego (company figures). — [Dealroom news](https://dealroom.co/news/156435-zoom-buys-ai-search-startup-onyx-to-win-data-sovereignty-customers/)
- Zoom's release cites more than 50 enterprise app connectors and a community of more than 30,000 developers. — [Zoom newsroom](https://news.zoom.com/zoom-acquires-onyx-adding-open-source-enterprise-knowledge-search-to-zoom-ai-services/)
- Vendor case study: Vespa.ai publishes an "Onyx" case study (Onyx as Vespa Cloud's customer). — [Vespa.ai](https://vespa.ai/onyx/)

### Inferences
- The logo list leans toward tech-forward, security-conscious companies (Bitwarden, Thales, NASA, Netflix). That fits the self-host and data-sovereignty pitch.

### Gaps
- onyx.app/customers could not be fetched, so any published quotes, metrics or case-study pages there are unverified. The search engine surfaced no onyx.app case-study text.

## 5. Rebrand, YC and funding announcements, and launch reception (HN / Product Hunt), plus the Zoom acquisition

### Takeaway
Danswer got strong HN reception from its 2023 Show HN onward. It went through YC W24 and rebranded to Onyx (late 2024; the exact date was not found). It raised a $10M seed (Khosla and First Round, March 2025), got a well-received but critical Launch HN as Onyx (~Nov 2025), and was acquired by Zoom on 2026-09-23 with a pledge to stay open source and self-hostable.

### Cited Findings
- HN history for founder account Weves:
  - "Show HN: Danswer – Open-source question answering across all your docs" (2023-07-10): 189 points.
  - Show HN: DanswerBot (2023-09).
  - Launch HN: Danswer (YC W24) (~Feb 2024).
  - "Launch HN: Onyx (YC W24) – Open-source chat UI": 254 points, 160 comments.
  — [HN submitted?id=Weves](https://news.ycombinator.com/submitted?id=Weves), [Show HN Danswer](https://news.ycombinator.com/item?id=36667374), [Launch HN Danswer](https://news.ycombinator.com/item?id=39467413), [Launch HN Onyx](https://news.ycombinator.com/item?id=46045987)
- Danswer Launch HN (2024): it connected 25 workplace tools. Some teams self-host open-source LLMs "to truly airgap the system." It matches source ACLs to users and groups, and it uses an LLM to judge each chunk ("Is this chunk <CHUNK> useful for answering this query <QUERY>"). — [Launch HN: Danswer](https://news.ycombinator.com/item?id=39467413)
- Onyx Launch HN reframed the product as an "Open-source chat UI" for any LLM. On LibreChat, the founder's reply pointed to "a large connector suite + good RAG and simpler UX." On Open WebUI, they argued others lack "(1) integration of external knowledge into the chat (2) simple UX (3) complex 'agent' flows." — [HN 46053399](https://news.ycombinator.com/item?id=46053399), [HN 46046957](https://news.ycombinator.com/item?id=46046957)
- The rebrand is a rename, not a fork: environment variables moved from DANSWER_* to ONYX_*. — [PromptQuorum review](https://www.promptquorum.com/power-local-llm/onyx-review)
- $10M seed, co-led by Khosla Ventures and First Round, with YC, Pioneer Fund and Twenty Two Ventures, announced 2025-03-12. Funds go to engineering, devrel and go-to-market. TechCrunch headline: "Why Onyx thinks its open source solution will win enterprise search." — [GlobeNewswire](https://www.globenewswire.com/news-release/2025/03/12/3041638/0/en/Onyx-Raises-10M-To-Help-Teams-Find-Knowledge-Faster-with-Open-Source-AI-Agents.html), [JustAINews](https://justainews.com/companies/funding-news/onyx-secures-10m-seed-led-by-khosla-ventures-and-first-round-capital/), [TechCrunch](https://www.techcrunch.com/2025/03/12/why-onyx-thinks-its-open-source-solution-will-win-enterprise-search/)
- Zoom acquisition, announced 2026-09-23, terms undisclosed. Onyx "will remain a standalone platform and stay open source, model-agnostic, app-agnostic and self-hostable." Zoom targets customers needing on-prem and strict data sovereignty. Co-founders Chris Weaver and Yuhong Sun joined Zoom. — [Zoom newsroom](https://news.zoom.com/zoom-acquires-onyx-adding-open-source-enterprise-knowledge-search-to-zoom-ai-services/), [Seedtable](https://seedtable.com/exits/onyx-2)
- Analysts question its future packaging: "it is not yet clear whether Onyx will remain a separate product, become part of Zoom AI Services, or appear inside a broader AI Companion package." — [remio.ai analysis](https://www.remio.ai/post/zoom-onyx-acquisition-targets-a-gap-cloud-first-ai-cannot-ignore), [UC Today](https://uctoday.com/ai-context-wars-microsoft-zoom-five9-fight-to-own-your-memory)
- Product Hunt: listing "Onyx: The open source AI teammate you've dreamed of", with no reviews at the time it was indexed. — [Product Hunt](https://www.producthunt.com/products/onyx-8)

### Inferences
- Community reaction to the Zoom deal was not found. Acquisitions of open-core projects often raise fears about license changes or roadmap drift, and Zoom's explicit "stays open source" pledge suggests it expected that concern. This is speculative.
- Repositioning from "enterprise search" (Danswer) to "open-source chat UI for any LLM" (Onyx, 2025) puts Onyx in direct competition with Open WebUI and LibreChat, where HN critics judge it on UX polish.

### Gaps
- No primary source for the exact Danswer to Onyx rename date or for how users reacted to it. Search did not find the announcement.
- The full Product Hunt launch stats (upvotes, rank) were not retrievable.

## 6. GitHub stats

### Takeaway
As of 2026-11-01, onyx-dot-app/onyx has about 33.1k stars and 4.7k forks. It is one of the largest open-source enterprise-search and chat repos, and it is very actively developed.

### Cited Findings
- 33,140 stars and 4,684 forks. GitHub reports 523 open issues plus PRs; the search API counts 167 open issues and 356 open PRs. The repo was created 2023-04-27 and last pushed 2026-11-01. The license reads "NOASSERTION" because the MIT core is mixed with `ee` code under a commercial license. Description: "Open Source AI Platform - AI Chat with advanced features that works with every LLM." — [GitHub repo](https://github.com/onyx-dot-app/onyx) (via gh search API)
- Issue volume by year: 161 (2023), 426 (2024), 389 (2025), 547 (2026 to date). About 12,138 merged PRs in total. — [GitHub issues](https://github.com/onyx-dot-app/onyx/issues)
- Zoom cites a community of more than 30,000 developers, which matches the star count. — [Zoom newsroom](https://news.zoom.com/zoom-acquires-onyx-adding-open-source-enterprise-knowledge-search-to-zoom-ai-services/)

### Inferences
- Issue volume dipped in 2025 and jumped in 2026. That matches the Nov 2025 relaunch as a general chat UI and a broader user base.

### Gaps
- The contributor count and star-history curve could not be retrieved (repo REST endpoints were blocked; star-history.com was not fetched).

## 7. Why teams pick Onyx over alternatives, and why some leave

### Takeaway
Teams pick Onyx when they want a self-hostable "open-source Glean": many connectors, permission mirroring, citations and a Slack bot. They also pick it when Open WebUI, LibreChat or AnythingLLM lack enterprise-knowledge integration, or when Glean's price ($45 to $65/user/month reported, versus $20 for Onyx Cloud) is too high. Teams leave or hesitate because of infrastructure weight, the engineering effort to self-host, enterprise features gated behind a commercial license, and chat UX less polished than Open WebUI's.

### Cited Findings
- Onyx is called the "closest one-to-one match" to Glean: 40+ connectors, permission-aware retrieval, chat with citations. Glean pricing is customer-reported at $45 to $65/user/month, against $20/user/month for Onyx Cloud. — [Open-source chat platforms compared](https://wz-it.com/en/blog/open-source-ai-chat-platforms-comparison/), [trickywombat Onyx alternatives](https://www.trickywombat.ai/onyx-alternatives), [neuralchainai Glean alternatives](https://neuralchainai.com/blog/top-open-source-glean-alternatives-enterprise-search-ai/)
- Against Open WebUI: Open WebUI's free edition includes OIDC/OAuth SSO, LDAP, RBAC, SCIM 2.0 and audit logs, but it lacks automatic permission mirroring from sources. Its license has a branding clause above 50 users. Onyx gates groups, RBAC and SSO behind paid tiers (SSO was later moved to MIT, per the HN maintainers). — [wz-it comparison](https://wz-it.com/en/blog/open-source-ai-chat-platforms-comparison/), [Open WebUI docs](https://docs.openwebui.com/alternatives/onyx.md), [Launch HN Onyx](https://news.ycombinator.com/item?id=46045987)
- Against AnythingLLM: it has three fixed roles and no groups, no OIDC, LDAP, SAML or SCIM, and about 20 connectors. It is "not a full enterprise search system." — [wz-it comparison](https://wz-it.com/en/blog/open-source-ai-chat-platforms-comparison/)
- Against LibreChat: the founder cites "a large connector suite + good RAG and simpler UX." — [HN 46046957](https://news.ycombinator.com/item?id=46046957)
- Users switching to Onyx: Open WebUI users cite its "general slowness and bugginess" and poor docs. — [HN 46052745](https://news.ycombinator.com/item?id=46052745)
- Reasons to hesitate or leave:
  - Self-hosting requires real engineering effort, and one guide says the connector catalog is smaller than some rivals'. — [Glean alternatives roundups](https://slite.com/learn/glean-alternatives), [Kestra](https://kestra.io/resources/ai/glean-alternatives)
  - "Stops where the enterprise features start": the `ee` directories carry a commercial license. — [Slite](https://slite.com/learn/glean-alternatives)
  - "Features ticked off a list"; indexed documents hard to trace. — [HN 46046530](https://news.ycombinator.com/item?id=46046530)
  - Mobile UI is broken compared with Open WebUI. — [GitHub #5618](https://github.com/onyx-dot-app/onyx/issues/5618)
- Competitors explicitly targeting Onyx users who do not want to host: Dust, Context-Link, amaiko and ibl.ai all publish "Onyx alternative" pages that pitch no infrastructure or added compliance and support. These are competitor marketing. — [Dust](https://dust.tt/blog/onyx-ai-alternatives-enterprise-knowledge-search), [Context-Link](https://www.context-link.ai/vs/onyx), [amaiko](https://amaiko.ai/compare/amaiko-vs-onyx), [ibl.ai](https://ibl.ai/blog/onyx-danswer-alternative-enterprise)
- Onyx itself publishes alternative and comparison pages (Glean alternatives, OpenWebUI alternatives, "OpenWebUI vs LibreChat vs Onyx"). — [onyx.app insights: Glean alternatives](https://onyx.app/insights/glean-alternatives), [onyx.app: OpenWebUI vs LibreChat vs Onyx](https://onyx.app/insights/openwebui-vs-librechat-vs-onyx)

### Inferences
- Onyx's competitive wedge is the knowledge layer (connectors, permission sync, Slack bot) delivered self-hosted. Its weak flanks are operational simplicity (Open WebUI and AnythingLLM are lighter) and free enterprise admin features (Open WebUI ships SSO, RBAC and SCIM free).
- The Zoom acquisition could cut both ways for buyers. It brings vendor stability and enterprise support for regulated buyers, but adds uncertainty about independence and roadmap for teams wary of a UC vendor. No user reactions were found to confirm either.

### Gaps
- No first-hand "we migrated from Glean/Guru/Copilot to Onyx" or "we left Onyx for X" case studies were found. Comparisons with Guru, Microsoft Copilot and Dify specifically were not found in user voices; only vendor roundups mention them.
