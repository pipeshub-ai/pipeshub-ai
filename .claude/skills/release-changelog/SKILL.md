---
name: release-changelog
description: Generate an enterprise-style changelog for a PipesHub GitHub release. Takes a release URL or tag (e.g. https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.5.0 or v0.5.0), writes the detailed per-release changelog to changelog/<version>.md, and registers it in the repo-root CHANGELOG.md (condensed entry + link) and the changelog/README.md index.
argument-hint: <release-url-or-tag>
---

# Release Changelog Generator

Turn a raw GitHub release (auto-generated "What's Changed" PR list) into a curated,
enterprise-grade changelog. The style is a hybrid of Linear (strong narrative headline +
explained highlights) and Keep a Changelog (categorized, scannable sections), tuned for
developers and operators who self-host PipesHub.

Two artifacts, one source of truth each way:

- `changelog/<version>.md` — the **detailed** per-release changelog (every PR
  represented, authors credited).
- `CHANGELOG.md` at the **repo root** — the **registry**: one condensed entry per
  release, newest first, each linking to its detailed file. (`changelog/README.md`
  keeps a one-row-per-release index table for folder browsing.)

## Step 1 — Resolve the release

The argument is a GitHub release URL or a bare tag.

- From a URL like `https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/v0.5.0`, the tag is the last path segment.
- Tags are inconsistent: some have a `v` prefix (`v0.5.0`), some don't (`0.1.0-beta`). If `gh release view` fails with the given form, retry with the prefix toggled.

Fetch the release:

```bash
gh release view <tag> --repo pipeshub-ai/pipeshub-ai --json tagName,name,publishedAt,isPrerelease,body
```

If the body is large, save it to a scratch file first and read it from there — never
summarize from a truncated body. Read the ENTIRE body before writing anything.

## Step 2 — Understand the material

The body is mostly `* <PR title> by @author in <PR url>` lines, possibly with a
"New Contributors" section and a "**Full Changelog**: ...compare..." link. PR titles are
terse; interpret them using this architecture context:

**PipesHub** is a workplace AI platform (enterprise search + agent workflows).
Polyglot system: Python FastAPI microservices (Connectors :8088, Indexing :8091,
Query/RAG :8000, Docling parsing :8081, Embedding :8002, Parsing :8092,
Extraction :8093), 1 Node.js Express API (auth/users/knowledge-base/storage), a
React/Next.js frontend, and an Electron desktop app. Stateful backends are pluggable:
graph (Neo4j/ArangoDB), vector (Qdrant/OpenSearch/Redis), MongoDB, KV (Redis/etcd),
broker (Kafka/Redis Streams). 30+ enterprise connectors (Google Workspace,
Microsoft 365, Slack, Jira, Confluence, GitLab, Notion, Salesforce, Zoom, OneDrive,
Outlook, SharePoint, S3, local filesystem…). AI layer: RAG pipeline, agent loop and
toolsets, MCP server, LiteLLM orchestration, knowledge graphs.

For the 4–7 PRs that anchor the Highlights section, you may enrich with:

```bash
gh pr view <num> --repo pipeshub-ai/pipeshub-ai --json title,body
```

Limit to ~8 such lookups per release; spend them on the biggest features.

## Step 3 — Write the detailed changelog file

Output path: `changelog/<version>.md` at the repo root, where `<version>` is the tag
without any `v` prefix (`0.5.0.md`, `0.4.0-beta.2.md`). Create the directory if needed.

### File format (follow exactly)

```markdown
# <version> — <Punchy headline conveying the main gist of the release>

**Released:** <Month D, YYYY> · **Channel:** <Stable | Beta | Alpha | Pre-release> · **Tag:** [`<tag>`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/<tag>)

<1–2 paragraph narrative summary: the themes of the release and what an
operator/developer gets by upgrading. Concrete, plain language, no hype.
Mention scale honestly (e.g. "120+ merged PRs").>

## Highlights

### <Major feature/theme 1>

<2–5 sentences: what it is, the problem it solves, operational implications.
Cite PRs inline: ([#2223](https://github.com/pipeshub-ai/pipeshub-ai/pull/2223) — @jatingaur18).>

### <Major feature/theme 2>
…
(3–7 highlights for large releases; 1–3 for small patch releases)

## Breaking changes & upgrade notes

<ONLY if PR titles clearly imply breaking/behavioral changes, port/config changes,
removals, or migrations. Otherwise omit the section entirely.>

## Connectors

- **<Connector name>** — <merged description of all its PRs this release> ([#N](link), [#M](link))

## Agents & AI

- <RAG, agents, tools, LLM, embedding, indexing, citations, prompts…> ([#N](link))

## Fixes

- <bug fixes not already covered above; merge duplicate/follow-up-fix PRs into one bullet> ([#N](link), [#M](link))

## Performance & reliability

- <perf, memory leaks, DB indexes, deadlocks, race conditions, rate limits> ([#N](link))

## Security & authentication

- <auth, SAML, JWT, OAuth, encryption, input validation> ([#N](link))

## Developer experience & infrastructure

- <CI, tests, docker, builds, SDK/OpenAPI spec, logging, docs> ([#N](link))

## New contributors

- @handle made their first contribution in [#N](link)
(only if the raw notes have a "New Contributors" section)

---

*Full commit-level detail: [GitHub release](<release url>) · [Compare view](<compare url from raw notes, if present>)*
```

### Rules

1. **Every PR in the raw notes must be represented** — in Highlights or a category
   bullet. Merging related PRs into one bullet is encouraged (cite all their numbers).
   Never silently drop a PR. Trivial CI churn may collapse into one bullet citing many
   numbers. *Exception:* for a founding release whose body enumerates the project's
   entire initial history (body > 50 KB), a thematic treatment with representative PR
   citations is allowed — but keep the full New Contributors list.
2. **Do not fabricate.** Derive descriptions from PR titles and the architecture
   context. If a title is cryptic, describe it conservatively rather than guessing at
   specifics.
3. **Credit authors** with @handle on highlights and notable bullets; keep credits
   wherever the raw notes name one clear author.
4. **The H1 headline must convey the release's single biggest story** — e.g.
   "0.5.0 — Salesforce and GitLab connectors mature, agents get file tools". Never
   generic ("Bug fixes and improvements") unless the release truly is only that.
5. Omit empty sections. Bullets are 1–2 sentences, sentence case.
6. PR links always as `[#1234](https://github.com/pipeshub-ai/pipeshub-ai/pull/1234)`.
7. For a pre-release later finalized by a stable release, add one line under the
   metadata line: `*This beta was finalized as [0.4.0](0.4.0.md).*` (link the sibling
   file if it exists).
8. Humanize the date from `publishedAt` (ISO 8601 UTC).
9. Channel: Stable for plain semver tags; Beta/Alpha per the tag suffix; trust the
   `isPrerelease` field for GitHub's own marking.

## Step 4 — Maintain the folder index

If `changelog/README.md` exists, insert/update this release's row (newest first):
`| [<version>](<version>.md) | <date> | <channel> | <H1 headline text> |` under the
columns `Version | Date | Channel | Summary`. If it doesn't exist, create it with a
short intro ("Curated changelogs for PipesHub releases, newest first.") and that table.

## Step 5 — Register the release in the root CHANGELOG.md

`CHANGELOG.md` at the **repo root** is the registry of all releases, newest first —
condensed entries only; the `changelog/<version>.md` file stays the detailed record.
Make two insertions:

1. A new row at the **top of the index table** (just under the header row):
   `| [<version>](#<anchor>) | <Mon D, YYYY> | <Channel> | <short theme, ≤8 words> |`
2. The new release entry directly below the `## Unreleased` section (after its `---`
   separator), ending with its own `---`, so the previous newest release slides down.
3. Refresh the `## Unreleased` compare link to start from the new tag:
   `[`<tag>...HEAD`](https://github.com/pipeshub-ai/pipeshub-ai/compare/<tag>...HEAD)`.

If the file doesn't exist, create it with this skeleton before inserting:

```markdown
# PipesHub Changelog

All notable changes to [PipesHub](https://github.com/pipeshub-ai/pipeshub-ai) — the workplace AI platform for enterprise search and agent workflows — documented in one place, newest first.

This file is the release registry: each entry is a condensed summary linking to the detailed per-release changelog in [`changelog/`](changelog/) (full PR-level accounting and author credits) and to the raw GitHub release. Versioning follows semver; **Stable** releases are recommended for production, **Beta/Alpha** channels preview the next stable.

| Version | Date | Channel | Theme |
|---|---|---|---|

---

## Unreleased

Changes merged to `main` since the last release: [`<tag>...HEAD`](https://github.com/pipeshub-ai/pipeshub-ai/compare/<tag>...HEAD).

---

*Maintained with the `release-changelog` skill (`.claude/skills/release-changelog/SKILL.md`). Detailed per-release changelogs live in [`changelog/`](changelog/).*
```

### Entry format (follow exactly)

```markdown
## <version> — <YYYY-MM-DD>

**<H1 headline from the detailed file>** · <Channel> · [`<tag>`](https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/<tag>) · [Full changelog](changelog/<version>.md)

<One- or two-sentence narrative: scale ("~195 PRs since 0.5.0") and theme.>

### Added
- <condensed bullet, citing only the 1–3 anchor PRs> ([#N](link))

### Changed
### Fixed
### Security
### Breaking changes & upgrade notes
```

Use only the sections that apply; 3–10 bullets total, distilled from the detailed
file's Highlights and Breaking sections — do not re-enumerate every PR. Always keep
the Breaking section when the detailed file has one.

- **Pre-releases:** append `· *Finalized as [<stable>](#<anchor>)*` to the metadata
  line once the stable ships; when writing the *stable* entry for a cycle that had a
  pre-release, add that pointer to the existing pre-release entry and open the stable
  narrative with "Finalizes [<pre>](#<anchor>) …".
- **Anchors:** GitHub's slug of the H2 — lowercase, dots and the em dash removed,
  spaces become hyphens (`## 0.4.0-beta.2 — 2026-05-02` → `#040-beta2--2026-05-02`).
  Verify the new index row's link matches the heading you actually wrote.
- Never rewrite existing registry entries except the pre-release pointer above.

## Step 6 — Report

Reply with the detailed file path, the H1 headline chosen, a one-line theme summary,
and confirmation that changelog/README.md and the root CHANGELOG.md were updated.
Do not paste the whole file into chat.
