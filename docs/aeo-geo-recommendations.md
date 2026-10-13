# PipesHub AEO / GEO recommendations

AEO, GEO, and AI SEO are one job: when someone asks an assistant “what is an open-source Glean alternative?”, the answer names PipesHub and cites a page you control, with the facts right.

The sentence below is the definition used in the GitHub README and docs changes. Paste it anywhere a buyer or an assistant reads first. Do not paraphrase it on the homepage, the vs-Glean page, or the GitHub About field.

> PipesHub is an Apache-2.0 workplace AI platform you run yourself. It connects company systems such as Slack, Google Drive, GitHub, Microsoft 365, and Notion, answers with a citation to the source block, and only returns what that person is allowed to open. The same context is available to agents, workflows, and MCP clients, using a model you choose.

Leave `docs.pipeshub.com/for-agents` and the `llms.txt` preamble as they are. Those pages tell a coding agent how to connect MCP. Buyer pages and agent pages should stay separate.

## Pull requests

These are open. They use the sentence above and do not include this file.

- GitHub README: https://github.com/pipeshub-ai/pipeshub-ai/pull/3992
- Docs intro and FAQ: https://github.com/pipeshub-ai/documentation/pull/238

## Already in those pull requests

**GitHub README** (`pipeshub-ai/pipeshub-ai`)

- Opening paragraph replaced with the canonical sentence.
- FAQ headings added for the questions assistants are actually asked: Is PipesHub open source? Can I self-host it? Does search follow source permissions? Is PipesHub an open-source alternative to Glean?
- Existing deploy, connector, model, and stack answers kept.

**Docs** (`pipeshub-ai/documentation`)

- Introduction rewritten to the same sentence.
- Removed the claim that answers use PageRank. The roadmap on that page still lists PageRank as not shipped. Saying it is live is a fact an assistant will repeat.
- FAQ gained a Product section with the same questions. The deploy troubleshooting questions stay below it.

## What you need to do

### 1. GitHub About text

The repository description is set in GitHub settings, not in the README. Today it says “40+ connectors” and “context layer for AI agents”. The README says 50+ connectors and workplace AI. Assistants blend the two.

Replace the About text with:

> Apache-2.0 workplace AI you run yourself. Permission-aware search and cited answers across Slack, Drive, GitHub, Microsoft 365, and 50+ connectors, plus MCP and SDKs. Self-hosted.

Settings → General → Description. This needs a GitHub admin. It cannot be changed in a pull request.

### 2. Marketing site (`pipeshub.com`)

The site repo is not in this workspace. On the homepage and on [pipeshub.com/pipeshub-vs-glean](https://pipeshub.com/pipeshub-vs-glean):

- Use the canonical sentence as the first paragraph.
- Add `Organization` and `FAQPage` JSON-LD. `sameAs` should list GitHub, docs, LinkedIn, X, Discord, and YouTube.
- Add the same FAQ headings as the README, as real questions with two-to-four sentence answers.
- Date the comparison page and update it when a fact changes.

Do not publish an Onyx comparison until someone who owns the product signs off on every cell. A wrong cell becomes the citation.

### 3. Pages that already outrank you

These are what retrieval finds for “open-source Glean alternatives” and “Glean vs PipesHub”:

- [onyx.app/insights/glean-alternatives](https://onyx.app/insights/glean-alternatives) calls PipesHub an early project and says it has no deep research and no comparable chat. The README lists deep research, chat, and agents.
- [faun.dev/toolbox/glean-vs-pipeshub](https://faun.dev/toolbox/glean-vs-pipeshub/) is a third-party comparison.

Ask those authors to correct the stale lines, and point them at the new FAQ. Until a newer page of yours is the one assistants cite, they will keep copying the old description.

### 4. Translated READMEs

`docs/i18n/*/README.md` still uses the old English definition in translation. Update those after the English sentence is final, so twelve languages do not each invent a different product.

### 5. Measure this monthly

Ask these in ChatGPT, Perplexity, Gemini, and Google AI Overviews. Record whether PipesHub is named, which URL is cited, and which claim is wrong. The next page to fix is the one that produced the wrong citation.

- What is an open-source alternative to Glean?
- What is PipesHub?
- PipesHub vs Onyx
- Self-hosted enterprise search that respects source permissions
- How do I connect company docs to Cursor or Claude Code?

## What not to change

- Do not put buyer slogans into `llms.txt`. Coding agents use that file to decide whether to call MCP.
- Do not claim PageRank, personalized search, or any other roadmap item as shipped.
- Do not say the entire connector catalogue is in the free build unless that is still true. A comparison that overclaims gets quoted, then contradicted.
