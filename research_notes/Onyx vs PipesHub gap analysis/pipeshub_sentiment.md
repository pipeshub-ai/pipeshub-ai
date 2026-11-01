# PipesHub user sentiment: what users love, what they complain about, why they choose it

Research date: 2026-11-01. Scope: pipeshub.com and GitHub pipeshub-ai/pipeshub-ai.

**Method and access limits. Read this before relying on any absence of evidence.**
- GitHub data came straight from the REST API (`gh api`) on 2026-11-01. I pulled all 314 issues (PRs excluded) and computed author mix, response times and a theme tally from that full set.
- The session network policy blocked direct fetches of reddit.com, hn.algolia.com, pipeshub.com, onyx.app, openalternative.co, neuralchainai.com and daniliants.com (DNS "ENOTFOUND" or "unable to fetch"). Web content therefore comes only from WebSearch result summaries and snippets. Treat those claims as second-hand. Any quote below marked "(snippet)" was not read on the full page.
- Several WebSearch queries for Reddit, Hacker News (Show HN) and Product Hunt returned **no PipesHub threads at all**. That is weak evidence that no large launch thread exists. It does not prove there is none, because Reddit and HN could not be queried directly.
- GitHub GraphQL is blocked in this session, so I could not count GitHub Discussions.

## Q1. What do users most appreciate about PipesHub? Which features get praised by name?

### Takeaway
Explicit praise is sparse and mostly found inside bug reports. Users like the idea of an open-source, self-hosted "Glean" with permission-aware connectors, and they credit the project once it works with Google Workspace. Answers with citations get named as a feature, but mostly in complaints about it. Third-party write-ups mostly praise the knowledge-graph-plus-vector design and the Apache-2.0 license, often in PipesHub's own framing.

### Cited Findings
- Open-source Glean is the strongest praise theme. "i love this idea to make opensource glean ai. I have closely worked with glean for my org and the concept of enterprise AI is transformative" — rishimalhan, 2025-08-21, [#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573).
  - The same user: "Currently, I am individually evaluating pipeshub and it works well with google workspace (besides a few quirks with pdf rendering in citations)."
  - A follow-up comment: "The initiative is super useful." The same comment asked for configurable RAG parameters and non-hardcoded prompts, and asked why the project did not use Vespa.
- "Hey guys, I just found this awesome project!" — Hurricane31337, 2025-09-08, in a request for i18n/German translation ([#646](https://github.com/pipeshub-ai/pipeshub-ai/issues/646)).
- "Hi team, Thanks for the great work on PipesHub." — jayesh-87, 2026-01-07, while asking for a fix to blank citation previews ([#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080)).
- Users who want new connectors offer to build them: "We use Odoo heavily and I would love to have a connector… I would be willing to contribute this connector" — StefanSpiess, 2026-04-10 ([#1870](https://github.com/pipeshub-ai/pipeshub-ai/issues/1870)).
- Keyword counts across all repo issues and comments (GitHub search, 2026-11-01):
  - "love": 5 issues
  - "awesome": 1
  - "amazing": 0
  - "great project": 0
  - "glean": 1
- Praise is a small share of the conversation (search via [GitHub API](https://github.com/pipeshub-ai/pipeshub-ai/issues)).
- Third-party positioning (snippets):
  - "built for teams that need accurate, explainable answers from their internal knowledge, without shipping sensitive data to a vendor's cloud… a self-hostable alternative to Glean."
  - "builds a knowledge graph across your connected apps, so queries return structured, reasoned results rather than fuzzy keyword matches."
  - It inherits source-app permissions rather than re-implementing them.
  - All of the above from a roundup surfaced via [cloudspress.com](https://www.cloudspress.com/best-glean-alternatives-for-companies-that-want-to-avoid-vendor-lock-in/) / [nexos.ai](https://nexos.ai/blog/glean-alternatives/) search results.
- Features named in listings (snippets):
  - Permission-aware search.
  - "explainable answers with precise block citations."
  - A knowledge graph on Neo4j/ArangoDB plus Qdrant.
  - MCP + SDKs to expose the knowledge graph to Claude, Cursor and VS Code.
  - Sources: [GitHub README](https://github.com/pipeshub-ai/pipeshub-ai), [openalternative.co/pipeshub](https://openalternative.co/pipeshub), [BrightCoding blog, 2026-07-03](https://www.blog.brightcoding.dev/2026/07/03/pipeshub-the-revolutionary-workplace-ai-platform-transforming-enterprise-search). A search summary flagged the BrightCoding comparison table as promotional.
- PipesHub's own Glean comparison page sells openness: you can "audit the code, fork it, self-host it, extend it, and contribute back" (snippet, vendor) — [pipeshub.com/pipeshub-vs-glean](https://pipeshub.com/pipeshub-vs-glean).

### Inferences
- The features users praise by name are:
  - an open-source, self-hosted Glean
  - the Google Workspace connector working
  - citations, which are valued enough that their failures get reported
- No user praise names the knowledge graph specifically. That praise comes from vendor and listing copy.
- Most praise is a preface to a bug report. That suggests goodwill toward the mission, with frustration over reliability.

### Gaps
- No Reddit, HN or Product Hunt user praise was reachable (see the access limits above).
- G2 and Capterra reviews: none surfaced in search. A G2 listing probably does not exist, but I did not verify that.
- Discord sentiment could not be read; it needs a login.

## Q2. What are the most common complaints, bugs, missing features and feature requests?

### Takeaway
Across the 84 issues filed by unaffiliated users (author_association NONE), the top themes are:
1. Setup, install and configuration breakage (~36%)
2. Connector failures, especially permissions and pagination in SharePoint, Confluence, Notion, Jira DC and Gmail (~24%)
3. Indexing and parsing failures, especially PDFs (~18%)
4. Citation and answer defects (~18%)

Recurring meta-complaints are docs that drift from the code, published images that lag fixes on main, and telemetry on by default.

### Theme frequency (estimated)
Base: 84 NONE-authored issues, 2025-05 to 2026-10. Classified by title keywords, and one issue can count under several themes. The 88 "CONTRIBUTOR" issues are excluded because many are templated `good first issue` contributor tasks.

| Theme | Issues | Share | Most-discussed examples |
| --- | --- | --- | --- |
| Setup / install / env config / Docker | ~30 | ~36% | #3030, #2951, #3016, #2503, #2546, #3054, #877, #3046 |
| Connectors (sync / permissions) | ~20 | ~24% | #1409 (15 comments), #1189 (10), #2701 (10), #2994, #3002, #1549 |
| Indexing / parsing (PDF, Excel, docling, embeddings) | ~15 | ~18% | #682 (23 comments, the most-commented issue), #710, #1565, #661, #2842 |
| Citations / answers / chat / agents | ~15 | ~18% | #1080 (12), #1071, #578, #2627 |
| Auth / permissions / SSO / invites | ~12 | ~14% | #1795 (10), #942, #943, #1414, #1435, #539 |
| UI / UX / i18n | ~11 | ~13% | #890, #646, #1116, #2427 |
| Model / provider integration | ~4 | ~5% | #3114, #1710, #607, #5126 |
| Privacy / telemetry | 1 by title (3 across all authors) | — | #3625, #3299, #3302 |
| Explicit praise | ~4–5 issues | — | #573, #646 |

### Cited Findings

**Indexing / PDF**

The most-commented issue in the repo, with 23 comments, is "Indexing of PDF documents regularly fails using default settings."
- Reporter: "After many hours, it just gives me FAILED for many documents… It would be helpful to get a debug log in console or progress bar while indexing runs." — henrikmcgee, 2025-09-18 ([#682](https://github.com/pipeshub-ai/pipeshub-ai/issues/682)).
- A second user: "I'm having the exact same issue… I also tried GEMINI embedding model with an API key which also didn't work."
- The maintainer found the cause was "under resourcing (RAM available to docker container)" after a switch from PyMuPDF to Docling, and merged a fix in 6 days. The issue was not formally closed until 2026-01-22.

**Docker installs are the default path**

The same reporter said: "Installing outside of docker just takes too much time." — [#682](https://github.com/pipeshub-ai/pipeshub-ai/issues/682).

**A "too buggy" verdict on record**

- "The project has so many bugs it's unusable." — mandevteam, 2025-12-20, on [#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080).
- Maintainer reply: "If there are any bugs please feel free to open issues and we will fix it. Project is used by many companies. There might be some corner case issues."

**Citations**

- The citation preview is blank — joggerbra, 2025-12-02, [#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080).
  - At least 4 users reported it.
  - Maintainers first said they could not reproduce it, then found the root cause and merged a fix on 2026-01-27 (8 weeks).
- The LLM generates invalid citations like "[Summary of UUID]" — emiel-os, 2025-12-01, [#1071](https://github.com/pipeshub-ai/pipeshub-ai/issues/1071).
- "Upon viewing the citation I see Error loading pdf." — rishimalhan, 2025-08-21, [#578](https://github.com/pipeshub-ai/pipeshub-ai/issues/578).

**SharePoint permissions and security**

- The connector failed for a tenant hosted in Europe because the code assumed US — [#1409](https://github.com/pipeshub-ai/pipeshub-ai/issues/1409). The patch was merged in 3 days.
- The user then raised access control: "The SharePoint connector in Pipeshub being accessible to all users raises a significant security concern… With over 10,000 files spread across 100+ sites, it's not feasible for us to audit… a gap here could pose a serious security risk." — adityaaktorai, 2026-02-06.
- Maintainers said native SharePoint permissions are respected, then later confirmed one folder-level permission scenario needed a fix (2026-02-19).
- A second report: "Only one user can see content. The other two users see no libraries or files, despite having equal or greater permissions." — mstcomit, 2026-02-25, with 200 sites and 400 users ([#1549](https://github.com/pipeshub-ai/pipeshub-ai/issues/1549)).

**Confluence**

Pages without explicit restrictions got "zero permission edges, making these records invisible to all users" — codingiran, 2025-12-31 ([#1189](https://github.com/pipeshub-ai/pipeshub-ai/issues/1189)).

**Notion**

- Full sync "terminates after ~2 search batches — 40 of 1,175 search-visible pages synced… logging '✅ Notion sync completed successfully'… no errors." — buritica, 2026-08-18 ([#2994](https://github.com/pipeshub-ai/pipeshub-ai/issues/2994)).
- Related issues from the same user:
  - `reindex_records()` is an unimplemented stub ([#2990](https://github.com/pipeshub-ai/pipeshub-ai/issues/2990))
  - an infinite user-permission pagination loop ([#2991](https://github.com/pipeshub-ai/pipeshub-ai/issues/2991))

**Jira Data Center**

The connector is "missing from chat picker for most users" in an org with "~1,600 active PipesHub users" on Neo4j — dominikcachovan4, 2026-07-13. It is still open ([#2701](https://github.com/pipeshub-ai/pipeshub-ai/issues/2701)).

**Gmail**

Shared-mailbox links always point to the owner's account, because the personalization was "a silent no-op" — 2026-08-19 ([#3002](https://github.com/pipeshub-ai/pipeshub-ai/issues/3002)).

**Local FS / incremental sync**

"Every sync is a full destructive re-embed, and a mid-backlog restart silently drops the queue" with ~600 PDFs — berkabot, 2026-07-30 ([#2842](https://github.com/pipeshub-ai/pipeshub-ai/issues/2842)). Fixed in 0.9.0, about 2 months later.

**Docs drift and release lag**

- The quickstart's `docker-compose.prod.yml` "no such file or directory" after a rename — elliottsj, 2026-08-11 ([#2951](https://github.com/pipeshub-ai/pipeshub-ai/issues/2951)). Fixed in one day.
- "Following the documented Quickstart today gives a deployment where the entire Python backend never starts. The UI loads normally on :3000, so the install looks successful, but every AI feature returns 503." The published `:slim` image predated the fix on main — AyushSingh110, 2026-08-21 ([#3030](https://github.com/pipeshub-ai/pipeshub-ai/issues/3030)).
- "README: Quickstart and FAQ describe two different deployment methods." All 12 locale READMEs still said docker-compose.prod.yml and "30+ connectors" — [#3016](https://github.com/pipeshub-ai/pipeshub-ai/issues/3016).

**Local / contributor setup**

- "the app shows the loading circle indefinitely" when following CONTRIBUTING.md — [#2503](https://github.com/pipeshub-ai/pipeshub-ai/issues/2503), 2026-06-07.
- Batches of dependency breakages: protobuf/etcd3 ([#2546](https://github.com/pipeshub-ai/pipeshub-ai/issues/2546)), `uuid` ESM ([#2499](https://github.com/pipeshub-ai/pipeshub-ai/issues/2499)), libmariadb-dev ([#2501](https://github.com/pipeshub-ai/pipeshub-ai/issues/2501)).
- A Chinese-language report of "Validation failed" on signup after a Docker deploy ([#1435](https://github.com/pipeshub-ai/pipeshub-ai/issues/1435)).

**Email / invite flow**

Reset and invite links were HTML-escaped and broken by mail scanners ([#1795](https://github.com/pipeshub-ai/pipeshub-ai/issues/1795), 10 comments, about 7 weeks to close). Root cause on the user side: a `FRONTEND_PUBLIC_URL` without a port.

**Privacy / telemetry**

- "I was not clearly informed that data collection would be enabled by default or that the instance would communicate with an external metrics collector… I later received a personalized welcome/outreach email from PipesHub." — hepeklab, 2026-09-27. Still open as of 2026-11-01, with no maintainer reply ([#3625](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625)).
- A contributor reported "no way to opt out before the first push — enableMetricCollection defaults to true" ([#3299](https://github.com/pipeshub-ai/pipeshub-ai/issues/3299)).
- A member filed that login telemetry "carry the user's email address" ([#3302](https://github.com/pipeshub-ai/pipeshub-ai/issues/3302)).
- This undercuts the self-hosting/privacy selling point.

**SSO / enterprise auth**

- SAML SSO lacks JIT provisioning, "defeating the purpose of Single Sign-On" — filoucool, 2025-10-30 ([#942](https://github.com/pipeshub-ai/pipeshub-ai/issues/942)). Closed in 5 days.
- A single-auth-method restriction creates admin lockout risk ([#943](https://github.com/pipeshub-ai/pipeshub-ai/issues/943)).

**MCP**

The MCP server was "incompatible with mcp-remote / Claude Desktop due to missing session ID", so Claude Desktop timed out — 2026-05-27 ([#2433](https://github.com/pipeshub-ai/pipeshub-ai/issues/2433)). Closed in 9 days.

**Feature requests (top by reactions or comments)**
- Email formats: eml, msg, mbox, pst ([#588](https://github.com/pipeshub-ai/pipeshub-ai/issues/588)). This is the top-reacted issue, though only 2 reactions.
- RSS connector ([#1228](https://github.com/pipeshub-ai/pipeshub-ai/issues/1228))
- Time-weighted retrieval ([#3015](https://github.com/pipeshub-ai/pipeshub-ai/issues/3015))
- FalkorDB graph backend ([#2444](https://github.com/pipeshub-ai/pipeshub-ai/issues/2444), 9 comments, filed by the FalkorDB CEO gkorland)
- Per-collection AI models, plus MinerU ([#1891](https://github.com/pipeshub-ai/pipeshub-ai/issues/1891))
- Odoo connector ([#1870](https://github.com/pipeshub-ai/pipeshub-ai/issues/1870))
- Microsoft 365 connector, early on ([#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573))
- Mobile UI ([#890](https://github.com/pipeshub-ai/pipeshub-ai/issues/890))
- Configurable RAG parameters ([#573 comment](https://github.com/pipeshub-ai/pipeshub-ai/issues/573))

**Overall engagement**

Reactions are very low across the tracker. The highest is 2 reactions, and almost every issue has 0, so the community is not upvote-driven ([GitHub search sort=reactions](https://github.com/pipeshub-ai/pipeshub-ai/issues)).

### Inferences
- The dominant pain is operational fragility rather than answer quality:
  - Docker resource limits
  - environment variables (`FRONTEND_PUBLIC_URL`, `CONNECTOR_PUBLIC_URL`, Qdrant TLS, Redis password)
  - stale published images and stale docs
- Connector bugs cluster in permission mapping and pagination. These are silent failures: records invisible to users, or a sync that reports success while incomplete. Enterprises evaluating permission-aware search will find this the most damaging class.
- Since August 2026, external reports have shifted from "it doesn't start" to deep, code-level diagnoses (Notion, Gmail, Local FS, telemetry). That suggests more sophisticated evaluators are running it at real scale, some with ~1,600 users.

### Gaps
- Theme shares are a keyword heuristic on titles, not manual coding of every issue.
- Discord complaints, which are probably the larger volume, could not be read.

## Q3. How is the community described, and how responsive are the maintainers?

### Takeaway
Maintainers, led by the CTO account `abhishek-pipeshub`, often answer within hours to a day and ship patches quickly on high-signal bugs. But only about 56% of unaffiliated users' issues got any maintainer comment, and the tail is long. Discord is the main support channel, and maintainers routinely redirect GitHub reporters to it.

### Cited Findings
- **First maintainer response.** Response by a MEMBER, OWNER or COLLABORATOR comment on the 84 NONE-authored issues (computed from the [GitHub API](https://github.com/pipeshub-ai/pipeshub-ai/issues), 2026-11-01):
  - 47 got a maintainer comment; 37 (44%) got none.
  - Among the 47: median first response 34.9 hours, p25 4.3 hours, p75 240 hours (10 days).
  - 22 got a reply within 24 hours and 31 within 72 hours.
  - Caveat: some issues may have been resolved by a linked PR without a comment.
- **Time to close.** Across 172 external issues (NONE + CONTRIBUTOR), 104 are closed and 68 are open. The median time to close for closed ones is 8 days.
- **Fast fixes:**
  - SharePoint region bug: diagnosed in 2 days, patched in 3 ([#1409](https://github.com/pipeshub-ai/pipeshub-ai/issues/1409)).
  - PDF/Docling RAM issue: fix merged in 6 days ([#682](https://github.com/pipeshub-ai/pipeshub-ai/issues/682)).
  - Docs rename breakage: fixed in 1 day ([#2951](https://github.com/pipeshub-ai/pipeshub-ai/issues/2951)).
- **Slow fixes and silences:**
  - Blank citation preview took 8 weeks, with an early "not able to reproduce" ([#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080)).
  - The telemetry issue had no reply after 5 weeks ([#3625](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625)).
  - "I emailed a security report to security@pipeshub.com on 2 September and received an automatic acknowledgment. The stated timeline (acknowledgment within 48h, updates every 7 days per SECURITY.md) has passed without any human response." — jack001-jk, 2026-09-15 ([#3293](https://github.com/pipeshub-ai/pipeshub-ai/issues/3293)).
- **Discord redirection.** Maintainers send GitHub reporters to Discord:
  - "If you can join our discord, we can identify the root cause easily" ([#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080))
  - "Please feel free to reach out to us on Discord so we can coordinate on scope, design, and implementation details." ([#1870](https://github.com/pipeshub-ai/pipeshub-ai/issues/1870))
  - Discord invite: https://discord.com/invite/K5RskzJBm2 (README).
- **Maintainer tone.** Polite and thankful: "Thanks for opening this feature request and for offering to contribute, really appreciate it" ([#1870](https://github.com/pipeshub-ai/pipeshub-ai/issues/1870)). Occasionally defensive: "Project is used by many companies. There might be some corner case issues." ([#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080)).
- **Contributor program.** 38 external issues carry `good first issue` and the tracker uses `first-timers-only` labels. External issue creation jumped from about 3–12 a month (H1 2026) to 30 in Aug, 21 in Sep and 50 in Oct 2026, which likely reflects a Hacktoberfest-style contributor push. Many are templated "Contributor task" issues (e.g. [#3054](https://github.com/pipeshub-ai/pipeshub-ai/issues/3054), [#3016](https://github.com/pipeshub-ai/pipeshub-ai/issues/3016)).
- **Spam.** Low-quality issues show up: crypto ("earn $MEEET on Solana", [#1703](https://github.com/pipeshub-ai/pipeshub-ai/issues/1703)) and "Free agent tools" ([#2808](https://github.com/pipeshub-ai/pipeshub-ai/issues/2808)). The repo has a `spam` label.

### Inferences
- Responsiveness is founder-driven and bimodal. Clear repros get same-day attention and fast patches. Hard-to-reproduce UI bugs, policy issues (telemetry) and the security inbox fall through.
- Much support happens in Discord, so the GitHub numbers understate total responsiveness, and its content is invisible to public evaluators.

### Gaps
- Discord member count and activity: the README carries a Discord shield, but I could not read its live value. Community descriptions on Reddit and HN were not reachable.

## Q4. What testimonials, case studies, customer logos and metrics appear on pipeshub.com?

### Takeaway
I found no verifiable named customer testimonials or case studies. pipeshub.com could not be fetched, and search surfaced none. Evidence of real deployments comes only from GitHub issue details.

### Cited Findings
- A search summary of the homepage: it describes "the open-source, enterprise-ready workplace AI platform", emphasizing self-hosting and model choice (snippet) — [pipeshub.com](https://pipeshub.com/).
  - It shows partner logos such as Asana and Slack, "but those appear to be SOC3 report listings, not endorsements" (search summary).
- A LinkedIn post describes an unnamed customer scenario: connecting tools into a single context layer let the system find changed behavior. It is not a verifiable testimonial (snippet) — [LinkedIn, Rishabh Gupta](https://www.linkedin.com/in/rish2394/).
- PipesHub was "selected for IIT Startups Cohort-16" (LinkedIn snippet) — [LinkedIn](https://www.linkedin.com/in/abhishek-gupta-54053a60/).
- A directory lists the company as founded in 2024 and headquartered in San Francisco — [gritt.io](https://www.gritt.io/company/pipeshub/).
- Founders per the site (snippet): Rishabh Gupta and Shekhar Kadyan (Co-Founders, Co-CEOs) and Abhishek Gupta (Co-Founder, CTO) — [pipeshub.com](https://pipeshub.com/).
- A hosted SaaS, PipesHub Cloud, is described with SOC 2 certification (snippet) — [search result summary of GitHub/pipeshub.com](https://github.com/pipeshub-ai/pipeshub-ai).
- Deployment-scale signals reported by users:
  - "~1,600 active PipesHub users" on Jira DC + Neo4j ([#2701](https://github.com/pipeshub-ai/pipeshub-ai/issues/2701))
  - "Sharepoint online whith 200 sites and 400 users" ([#1549](https://github.com/pipeshub-ai/pipeshub-ai/issues/1549))
  - "over 10,000 files spread across 100+ sites" ([#1409](https://github.com/pipeshub-ai/pipeshub-ai/issues/1409))
- Maintainer claim: "Project is used by many companies" ([#1080](https://github.com/pipeshub-ai/pipeshub-ai/issues/1080)).
- Pricing (snippet): no public per-seat price was surfaced for PipesHub. Onyx's cloud is about $20/user/month, and Glean's buyer-reported range is $50–75/user/month with a 100-seat minimum — [makerstack Onyx review](https://makerstack.co/reviews/onyx-review/), [nexos.ai](https://nexos.ai/blog/glean-alternatives/).

### Inferences
- PipesHub's social proof is weak compared with Onyx and Glean: no named logos or case studies were found. Evaluators rely on GitHub stars and the Apache-2.0 license instead.

### Gaps
- The pricing page, logo wall and any case-study pages on pipeshub.com could not be fetched. pipeshub.com must be allowed in the session network settings to verify them.

## Q5. How was the launch received on Hacker News, Product Hunt and Reddit?

### Takeaway
I found no evidence of a notable Show HN, Product Hunt or Reddit launch thread. The main press item is a heise.de story on the 1.0.0 release (October 2026).

### Cited Findings
- WebSearch for "PipesHub" with "Show HN" returned no PipesHub post ([search, 2026-11-01](https://news.ycombinator.com/showhn.html)).
  - hn.algolia.com was not reachable from this session.
- WebSearch for "PipesHub Product Hunt" returned no PipesHub listing.
- WebSearch for "PipesHub" with the r/selfhosted, r/LocalLLaMA and r/Rag subreddit names returned no PipesHub threads.
  - reddit.com was not fetchable.
- heise.de, "AI agents on a short leash: PipesHub searches company knowledge" (snippet). It reports the release of version 1.0.0 with "code graphs for repository analysis, tighter guardrails for AI tools and a revamped search pipeline" — [heise.de](https://www.heise.de/en/news/AI-agents-on-a-short-leash-PipesHub-searches-company-knowledge-11503145.html).
  - GitHub confirms v1.0.0 was published on 2026-10-27 ([releases](https://github.com/pipeshub-ai/pipeshub-ai/releases)).
- Other listings:
  - [AlternativeTo](https://alternativeto.net/software/pipeshub/about/)
  - [SourceForge mirror](https://sourceforge.net/projects/pipeshub.mirror/)
  - [opensourcesoftware.io](https://opensourcesoftware.io/software/pipeshub/)
  - [dev.co](https://dev.co/ai/rag/pipeshub-ai) (shows a stale v0.5.0)
  - [techjockey](https://www.techjockey.com/us/detail/pipeshub)
  - I could not read the contents of any of them.

### Inferences
- Growth appears driven by GitHub discovery, listicles and alternative-directories, plus founder LinkedIn, rather than a single viral launch. The star curve in Q6 shows no one-day spike in the sampled points.

### Gaps
- An HN or Reddit search needs hn.algolia.com and reddit.com allowed in network settings. Founder-authored Reddit posts and their replies could not be verified.

## Q6. GitHub stats: stars, forks, contributors, growth trajectory, issue counts

### Takeaway
As of 2026-11-01 the repo has 3,899 stars, 631 forks and 87 contributors. Star growth peaked in autumn 2025 at about 250/month, slowed in H1 2026 to about 115/month, and recovered to about 200–250/month in mid/late 2026. Releases are now roughly biweekly, reaching 1.0.0 on 2026-10-27.

### Cited Findings
Source for all figures: [GitHub API](https://github.com/pipeshub-ai/pipeshub-ai), 2026-11-01.

- **Repo metadata:**
  - Created 2025-03-06
  - 3,899 stars, 631 forks, 17 watchers
  - Apache-2.0
  - Last push 2026-11-01
- **Topics.** They include `glean`, `graphrag`, `knowledge-graph`, `mcp-server`, `self-hosted`, `context-layer` and `claude-code`.
- **Contributors.** 87, including anonymous ones.
- **Issues.** 315 total via search (PRs excluded): 107 open, 208 closed.
  - Author mix: 142 MEMBER, 88 CONTRIBUTOR, 84 NONE.
  - Label counts: 59 `bug`, 6 `enhancement`.
- **Star trajectory** (sampled from stargazer timestamps):

  | Star # | Date |
  | --- | --- |
  | #1 | 2025-03-17 |
  | #401 | 2025-05-21 |
  | #901 | 2025-08-18 |
  | #1,401 | 2025-10-15 |
  | #1,901 | 2025-11-12 |
  | #2,401 | 2026-01-22 |
  | #2,901 | 2026-06-05 |
  | #3,401 | 2026-08-19 |
  | #3,801 | 2026-10-06 |
  | 3,899 | 2026-11-01 |

  - Roughly 500 stars in Oct 15 – Nov 12, 2025 (the fastest month).
  - About 500 stars over Jan 22 – Jun 5, 2026 (about 115/month).
  - About 500 stars per roughly 2.5 months since June 2026.
- **Releases:**
  - v0.8.0, 2026-09-14
  - v0.9.0 and v0.9.1, 2026-09-28
  - v0.10.0, 2026-10-12
  - v1.0.0, 2026-10-27
- **Comparisons with Onyx:**
  - openalternative compare (snippet): about 32,000 Onyx stars vs about 3,700 PipesHub, "though PipesHub is growing faster" — [openalternative.co/compare/onyx/vs/pipeshub](https://openalternative.co/compare/onyx/vs/pipeshub).
  - Onyx's own guide (snippet, stale): "roughly 1,000+ GitHub stars versus Onyx's 17,000+" — [onyx.app/insights/glean-alternatives](https://onyx.app/insights/glean-alternatives).
- **Contradictory claim.** A LinkedIn post snippet "cites 30,000+ GitHub stars", which conflicts with the API count of 3,899. It probably refers to Onyx or the category, and is unverified ([search summary](https://www.linkedin.com/in/rish2394/)).

### Inferences
- The forks-to-stars ratio of about 16% is high for an app repo. That is consistent with self-hosters forking to customize, and with the contributor program.
- PipesHub is about 8–10x smaller than Onyx by stars.

### Gaps
- No exact star-history chart data was collected. The README embeds a star-history.com chart, so day-level spikes may be missed.

## Q7. Social presence: X/Twitter, LinkedIn, YouTube

### Takeaway
Visible social activity is mainly the founders' LinkedIn profiles. I found no follower counts or engagement metrics for X, LinkedIn or YouTube.

### Cited Findings
- Founder LinkedIn profiles describe "Building PipesHub" and "Building Open-source Data Infrastructure Platform that helps Teams build AI Agents" (snippet) — [Rishabh Gupta](https://www.linkedin.com/in/rish2394/), [Abhishek Gupta](https://www.linkedin.com/in/abhishek-gupta-54053a60/).
- A search found no X/Twitter account details ([search summary](https://pipeshub.com/)).
- The repo has a `social-media` label, used on at least one external issue ([labels](https://github.com/pipeshub-ai/pipeshub-ai/labels)).

### Inferences
- Marketing appears LinkedIn-led and founder-led, with low visible engagement on X and YouTube.

### Gaps
- Follower counts and engagement for X, LinkedIn and YouTube could not be retrieved; those sites cannot be fetched from this session.

## Q8. Why do people pick PipesHub over alternatives (Onyx/Danswer, Glean, AnythingLLM, Open WebUI), and where is it lacking?

### Takeaway
Users and listings pick PipesHub for four reasons:
- an Apache-2.0, self-hostable Glean alternative
- a knowledge graph on top of vector RAG
- permission-aware connectors (Microsoft 365, SharePoint, Jira DC, Confluence)
- MCP/agent exposure

Its weaknesses next to Onyx are maturity, community size, connector polish and features (deep research, the chat UI), as described in competitor and third-party write-ups. I found no hands-on, user-written PipesHub-vs-Onyx comparison.

### Cited Findings
- Picked as an open-source Glean, from someone who has used Glean: "I have closely worked with glean for my org… i love this idea to make opensource glean ai" ([#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573), 2025-08-21).
- PipesHub's framing (vendor): Onyx "relies on vector retrieval", while PipesHub "adds a knowledge graph on top of vector and agentic RAG" and links related items across Slack, Jira and Confluence (snippet) — [pipeshub.com/pipeshub-vs-onyx](https://pipeshub.com/pipeshub-vs-onyx).
- Onyx-authored assessment (competitor; snippet): PipesHub is "earlier-stage than Onyx, with a smaller connector library, a smaller community, and fewer production enterprise deployments" and "lacks deep research and an AI chat interface comparable to Onyx's" — [onyx.app/insights/glean-alternatives](https://onyx.app/insights/glean-alternatives).
  - The same piece calls PipesHub an "emerging contender."
- Conflicting connector counts:
  - One comparison table lists PipesHub with 50+ connectors vs Onyx's 40+, while the same article's text says PipesHub's list is smaller (snippet) — [neuralchainai.com](https://neuralchainai.com/blog/top-open-source-glean-alternatives-enterprise-search-ai/).
  - The GitHub repo description says "40+ connectors". The README says 50+, and older locale READMEs say 30+ ([#3016](https://github.com/pipeshub-ai/pipeshub-ai/issues/3016)).
- License as a differentiator: PipesHub is Apache-2.0. Onyx is MIT outside its `ee` directories, which carry the Onyx Enterprise License (snippet) — [openalternative compare](https://openalternative.co/compare/onyx/vs/pipeshub).
- AnythingLLM and Open WebUI are described as document-chat tools based on manual upload, not connector-based enterprise search. PipesHub is "the newest entrant, worth a pilot" (snippet) — [blog.premai.io](https://blog.premai.io/10-best-anythingllm-alternatives-for-enterprise-document-ai-2026/), [wz-it.com](https://wz-it.com/en/blog/open-source-ai-chat-platforms-comparison/).
- A user asked why not Vespa, with re-ranking "baked in", and wanted configurable RAG parameters (number of docs, search strategy) — [#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573).
  - This is a sign that technical evaluators compare the retrieval stacks directly.
- Maintainers benchmark against Onyx internally: "the CLI does the real work (same shape as Onyx'…)" — PipesHub co-founder shekharkadyan, 2026-08-26 ([#3074](https://github.com/pipeshub-ai/pipeshub-ai/issues/3074)).
- No independent evaluation of the knowledge-graph retrieval claims was found (search summary, 2026-11-01).
- Where users found it lacking, beyond Q2:
  - SharePoint access control at scale ([#1409](https://github.com/pipeshub-ai/pipeshub-ai/issues/1409))
  - Microsoft 365 coverage early on ([#573](https://github.com/pipeshub-ai/pipeshub-ai/issues/573))
  - incremental sync for local files ([#2842](https://github.com/pipeshub-ai/pipeshub-ai/issues/2842))
  - telemetry defaults that conflict with the self-hosted privacy pitch ([#3625](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625))
  - SAML JIT provisioning ([#942](https://github.com/pipeshub-ai/pipeshub-ai/issues/942))

### Inferences
- PipesHub's real differentiators in users' own words are open source, self-hosting, the Glean-like experience and the Microsoft/Atlassian connectors. The knowledge-graph advantage is mostly vendor-asserted and unproven by users.
- The gaps users would weigh against Onyx are reliability of connector permission sync, ease of install, docs accuracy and community size. These line up with Onyx's claimed maturity advantage.

### Gaps
- No user-authored head-to-head PipesHub-vs-Onyx review was reachable (Reddit and HN blocked).
- The full pipeshub-vs-onyx page and the neuralchainai and openalternative pages could not be fetched. The quotes above are search-snippet paraphrases, and their exact wording should be verified once pipeshub.com, onyx.app and openalternative.co are allowed.
