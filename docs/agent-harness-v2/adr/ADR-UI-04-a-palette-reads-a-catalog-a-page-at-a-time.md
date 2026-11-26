# ADR-UI-04: A palette reads a catalog a page at a time

- Package: R31-LAT (STATUS R30-LAT-FU-1).
- Related: ADR-UI-03 (an admin list names what it holds), VOL-2 (the builder of an agent with many grants), WP-100
  (the builder's environment), `frontend/tests/e2e/perf/volume-budgets.json` (`builder-catalogs`, pre-registered
  before its first measurement).

## Context

The agent builder's palette offers the org's collections, connected apps and actions. On load it read each catalog
to its last page: `GET /api/v1/knowledgeBase/?page=N&limit=100` until a short page, `knowledge-hub/nodes` by cursor
until none, `toolsets/my-toolsets` 20 at a time until `hasNext` was false. Its requests grew with the org, not with
the agent: 16 with three of each, 49 with 500 (6 + 5 + 25 catalog pages), and the canvas waited for all of them.
Every one of those APIs already pages and searches on the server (`search`, `q`, `search`). The palette searched
only what it had read, so reading everything was what made its search complete.

## Decision

D1. **A catalog is read a page at a time.** The builder's load reads the first page of each catalog (100 rows: the
collections API's maximum, and the same for apps and actions), whatever the org holds. "Show more" first unfolds the
rows read, then reads the next page; it counts what the org holds when the server says (collections' `totalCount`).

D2. **The palette's search asks the server.** One search per pause in typing (300 ms) asks each catalog's first page
of matches (`search`, `q`, `search`). The palette shows the pages read and the hits not among them, and still
filters them locally (a query naming a section or a tool inside an action keeps working on what is read).

D3. **The agent's own grants never come from a catalog.** The agent's answer holds its toolsets, knowledge and their
names (VOL-2); rebuilding the canvas reads only that. A legacy agent whose collection was saved as a record group
before the per-collection model is matched against the first page only (see Deviations).

D4. **"All of them" reads the rest on demand.** Dropping the Collections or Apps group means every collection or app,
not the pages read: with such a group on the canvas, the builder reads the remaining pages once and gives the group
node the whole list. Nothing else reads a catalog whole.

## As built

- `frontend/app/(main)/agents/agent-builder/hooks/paged-catalog.ts` (`catalogSteps`: first, more, rest, search, as
  pure state steps), `hooks/use-paged-catalog.ts` (the React state, newer list or search wins), readers
  `AgentsApi.knowledgeBasePage` and `knowledgeHubAppPage` (`agents/api.ts`; the whole-catalog loaders are gone) and
  `readToolsetPage` (`use-agent-builder-data.ts`, the person's or a service account's actions).
- `components/palette-show-more.tsx` (one "Show more" for Collections, Apps and Actions), the sidebar's debounced
  `onSearchCatalogs`, and the group completion in `agent-builder.tsx`.
- Tests: `perf/volume-lists.perf.spec.ts` VOL-8 (requests at 3 and 500 of each) and VOL-8b (a collection, an app and
  an action past the first page found by the palette), `agents/__tests__/api.test.ts` (both readers),
  `agent-builder/__tests__/palette-search.test.tsx` (one server search per pause; Show more reads the next page).
- Measured (VOL-8, every API served, base `432ae2ecd6` → after): 16 → 49 requests from 3 to 500 of each catalog,
  ready at 500 in 1,345 ms, before; 16 → 16, ready in 745 ms, after. VOL-8b passes on both.
- No server change: every API already paged and searched.

## Deviations

- A legacy agent (saved before the per-collection model) whose collection is not on the first page is drawn as an
  app knowledge node, not a collection node. The migration (#2650) gave such entries `type: KB`, which is read first,
  so only an unmigrated agent can be affected.
- Actions are grouped by type in the palette, so their "Show more" gives no count.

## Owner amendments

None yet.

## Status

**Accepted** 2026-11-12: decided from first principles, long term, UX-first.
