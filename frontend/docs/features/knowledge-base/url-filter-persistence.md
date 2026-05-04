# URL Filter Persistence — Knowledge Base Page

## Overview

The knowledge-base page persists filter, sort, pagination, and search state in URL query parameters. This makes every view shareable: copying a URL and opening it in a new tab restores the exact same data view.

---

## URL Parameter Schema

Array values are comma-separated. Default values are omitted from the URL.

| Param | State Field | Example | Default (omitted) |
|-------|-------------|---------|-------------------|
| `recordTypes` | `recordTypes` | `FILE,WEBPAGE` | _(none)_ |
| `indexingStatus` | `indexingStatus` | `COMPLETED,FAILED` | _(none)_ |
| `sizeRanges` | `sizeRanges` | `lt1mb,1to10mb` | _(none)_ |
| `origins` | `origins` (all-records only) | `COLLECTION,CONNECTOR` | _(none)_ |
| `connectorIds` | `connectorIds` (all-records only) | `uuid-one,uuid-two` | _(none)_ |
| `createdAfter` | `createdAfter` | `2024-01-01` | _(none)_ |
| `createdBefore` | `createdBefore` | `2024-12-31` | _(none)_ |
| `createdDateType` | `createdDateType` | `on` \| `between` \| `before` \| `after` | _(none)_ |
| `updatedAfter` | `updatedAfter` | `2024-01-01` | _(none)_ |
| `updatedBefore` | `updatedBefore` | `2024-12-31` | _(none)_ |
| `updatedDateType` | `updatedDateType` | `on` \| `between` \| `before` \| `after` | _(none)_ |
| `sortField` | sort field | `name` | `updatedAt` |
| `sortOrder` | sort order | `asc` | `desc` |
| `page` | page number | `3` | `1` |
| `limit` | page limit | `25` | `50` |
| `search` | search query | `sales+docs` | _(none)_ |

These are appended to the existing navigation params (`view`, `nodeType`, `nodeId`) which are unchanged.

**Example URL:**
```
/knowledge-base?nodeType=kb&nodeId=abc123&recordTypes=FILE,WEBPAGE&indexingStatus=COMPLETED&page=2&sortField=name&sortOrder=asc
```

---

## File Locations

| File | Role |
|------|------|
| `app/(main)/knowledge-base/url-params.ts` | Serialize/parse URL params (pure utility, no React) |
| `app/(main)/knowledge-base/page.tsx` | Hydration effect, URL sync effect, `buildNavUrl` |
| `app/(main)/knowledge-base/store.ts` | `hydrateFilter`, `hydrateAllRecordsFilter` actions |

---

## Architecture: Two-Way Sync

The URL is kept in sync with store state in both directions:

```
URL params (on page load)
      │
      ▼  [Hydration effect — runs once on mount]
  Zustand store
      │
      ▼  [fetchTableData / fetchAllRecordsTableData — reads getState()]
   API call (with correct filters)
      │
      ▼  Data displayed

User changes filter via FilterBar
      │
      ▼  setFilter / setAllRecordsFilter → Zustand store
      │
      ▼  [URL sync effect — watches filter/sort/pagination]
  router.replace(newUrl)
      │
      ▼  searchParams changes
      │
      ▼  [Fetch effect — [searchParams, fetchTableData]]
   API call (with new filters)
```

---

## Hydration (URL → Store)

**File:** `page.tsx` — `useEffect([], [])` (runs once on mount)

On initial page load, the hydration effect:
1. Parses all filter/sort/pagination/search params from `searchParams` using `parseCollectionsParams` or `parseAllRecordsParams` from `url-params.ts`
2. Calls **`hydrateFilter`** (not `setFilter`) to replace the filter state without triggering a pagination reset
3. Sets sort, limit, then page — in that order (sort/limit reset page internally; page is set last to restore the URL value)
4. Sets search query if present, opens the search bar

```typescript
// Hydration runs once on mount — empty deps
useEffect(() => {
  if (hasHydratedFromUrl.current) return;
  hasHydratedFromUrl.current = true;

  const store = useKnowledgeBaseStore.getState();
  const parsed = parseCollectionsParams(searchParams); // or parseAllRecordsParams

  if (Object.keys(parsed.filter).length > 0) store.hydrateFilter(parsed.filter);
  if (parsed.sort.field !== 'updatedAt' || parsed.sort.order !== 'desc') store.setSort(parsed.sort);
  if (parsed.limit !== 50) store.setCollectionsLimit(parsed.limit); // resets page to 1 internally
  if (parsed.page !== 1)  store.setCollectionsPage(parsed.page);   // restore page LAST
  if (parsed.searchQuery) { store.setSearchQuery(parsed.searchQuery); setIsSearchOpen(true); }
}, []);
```

**Why `hydrateFilter` instead of `setFilter`?**

`setFilter` resets `collectionsPagination.page` to 1 as a side effect. During hydration we want to restore the exact page from the URL, so we use `hydrateFilter` which replaces the filter state without any side effects.

---

## Fetch Functions Read from `getState()`

**File:** `page.tsx` — `fetchTableData` and `fetchAllRecordsTableData`

Both fetch functions always read filter/sort/pagination/search **fresh from the store** using `useKnowledgeBaseStore.getState()`, not from the React closure:

```typescript
const fetchTableData = useCallback(async (nodeType, nodeId) => {
  const currentState = useKnowledgeBaseStore.getState();
  const currentFilter     = currentState.filter;
  const currentSort       = currentState.sort;
  const currentPagination = currentState.collectionsPagination;
  const currentSearchQuery = currentState.searchQuery;

  const params = buildFilterParams(
    { ...currentFilter, searchQuery: currentSearchQuery },
    currentSort,
    currentPagination,
  );
  // ...
}, [filter, sort, ...]); // filter/sort still in deps to re-memoize on UI changes
```

`filter` and `sort` remain in the `useCallback` deps array so the function reference updates when they change (triggering the fetch effect), but the actual API params come from `getState()` to avoid stale closure values on initial load.

**Why this matters:** React effects fire in registration order within the same commit. The hydration effect (registered first) writes to the store synchronously. When the fetch effect runs next, `getState()` already returns the hydrated values, so the first API call includes the URL filters.

---

## URL Sync (Store → URL)

**File:** `page.tsx` — `useEffect([filter, sort, collectionsPagination.page, ...], [])`

Whenever filter, sort, pagination, or debounced search changes, the URL sync effect:
1. Skips the very first render (before hydration) via `isFirstUrlSyncRender` ref
2. Serializes the current store state using `serializeCollectionsParams` or `serializeAllRecordsParams`
3. Preserves existing navigation params (`view`, `nodeType`, `nodeId`)
4. Calls `router.replace(newUrl)` only if the URL actually changed (avoids unnecessary history entries)

`router.replace` (not `router.push`) is used to avoid polluting browser history with every filter change.

```typescript
useEffect(() => {
  if (isFirstUrlSyncRender.current) { isFirstUrlSyncRender.current = false; return; }
  if (!hasHydratedFromUrl.current) return;

  const baseParams = { ...(isAllRecordsMode ? { view: 'all-records' } : {}) };
  const nodeType = searchParams.get('nodeType');
  const nodeId   = searchParams.get('nodeId');
  if (nodeType) baseParams.nodeType = nodeType;
  if (nodeId)   baseParams.nodeId   = nodeId;

  const filterParams = serializeCollectionsParams(filter, sort, collectionsPagination, debouncedSearchQuery);
  const newUrl = buildFilterUrl(baseParams, filterParams);
  if (newUrl !== currentUrl) router.replace(newUrl);
}, [filter, sort, collectionsPagination.page, collectionsPagination.limit, debouncedSearchQuery, ...]);
```

---

## Navigation Preserves Filters

**File:** `page.tsx` — `buildNavUrl`

`buildNavUrl` is used for all navigation within the same mode (sidebar clicks, breadcrumb clicks, double-click into folder). It now serializes the current filter/sort/pagination into every navigation URL so active filters survive navigation:

```typescript
const buildNavUrl = (navParams) => {
  const store = useKnowledgeBaseStore.getState();
  const filterParams = isAllRecordsMode
    ? serializeAllRecordsParams(store.allRecordsFilter, store.allRecordsSort, ...)
    : serializeCollectionsParams(store.filter, store.sort, ...);

  return buildFilterUrl({ ...baseParams, ...navParams }, filterParams);
};
```

---

## Mode Switching Resets Filters

When switching between **Collections** and **All Records** modes, filters are intentionally not carried over. The mode-switch `useEffect` (watching `pageViewMode`) clears search/filter state, but skips the initial mount to avoid interfering with URL hydration.

---

## URL Params Utility (`url-params.ts`)

**File:** `app/(main)/knowledge-base/url-params.ts`

Pure TypeScript module (no React imports) with four exports:

| Function | Direction | Description |
|----------|-----------|-------------|
| `serializeCollectionsParams(filter, sort, pagination, searchQuery)` | Store → URL | Converts Collections mode state to URL param key-value pairs |
| `serializeAllRecordsParams(filter, sort, pagination, searchQuery)` | Store → URL | Same for All Records mode (includes `ori` param) |
| `parseCollectionsParams(searchParams)` | URL → Store | Parses URL into `{ filter, sort, page, limit, searchQuery }` |
| `parseAllRecordsParams(searchParams)` | URL → Store | Same for All Records mode |

Serialize functions skip any value that equals its default (keeps URLs clean). Parse functions fall back to defaults when a param is absent or invalid.

---

## Store Actions

Two new actions were added to `store.ts` for URL hydration:

| Action | Behavior |
|--------|----------|
| `hydrateFilter(filter)` | Replaces `filter` state directly — no pagination reset side effect |
| `hydrateAllRecordsFilter(filter)` | Same for `allRecordsFilter` |

These are distinct from `setFilter`/`setAllRecordsFilter` which reset page to 1 (intentional UX for filter changes, but wrong for URL restore).
