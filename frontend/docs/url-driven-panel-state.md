# URL-Driven Panel State Pattern

Sidebar panels (right panels, dialogs) in admin/workspace pages persist their open/closed state in URL query parameters. This makes panel state **shareable** (via link) and **reload-safe** (no storage needed).

## URL Schemes

### Groups Page (`/workspace/groups`)

| State | URL |
|-------|-----|
| No panel open | `/workspace/groups/` |
| Create Group panel | `/workspace/groups/?panel=create` |
| View Group panel | `/workspace/groups/?panel=detail&groupId=<id>` |
| Edit Group panel | `/workspace/groups/?panel=detail&groupId=<id>&mode=edit` |

### Users Page (`/workspace/users`)

| State | URL |
|-------|-----|
| No panel open | `/workspace/users/` |
| Invite Users panel | `/workspace/users/?panel=invite` |

### Query Parameters

| Param | Values | Purpose |
|-------|--------|---------|
| `panel` | `create`, `detail`, `invite` | Which sidebar panel is open |
| `groupId` | MongoDB ObjectId | Which group to show in detail panel |
| `mode` | `edit` | Whether the panel is in edit mode |

## How It Works

Two effects sync URL ↔ Store, with a **string-based ref** preventing feedback loops:

```
User clicks "Create Group" button
  → router.push('?panel=create')               [explicit navigation]
  → searchParams changes
  → URL→Store effect fires → openCreatePanel()  [URL drives store]

User clicks X to close panel
  → closeCreatePanel()                          [store action from sidebar]
  → isCreatePanelOpen becomes false
  → Store→URL effect fires → router.replace('/workspace/groups/')  [store drives URL]
  → searchParams changes
  → URL→Store effect fires → pendingUrlRef matches → SKIP  [loop broken]
```

## Implementation Pattern (3 pieces)

### 1. Refs for Loop Prevention

```tsx
// Key = "panel|groupId|mode" — identifies URL changes we caused ourselves
const pendingUrlRef = useRef<string | null>(null);
// Prevents closing panels before the initial page load completes
const initialUrlProcessed = useRef(false);

const buildUrlKey = useCallback(
  (panel: string | null, groupId: string | null, mode: string | null) =>
    `${panel ?? ''}|${groupId ?? ''}|${mode ?? ''}`,
  []
);
```

### 2. URL → Store Effect

Reads query params and opens/closes panels. Uses `useXxxStore.getState()` to read
current store state WITHOUT adding it as a dependency (prevents effect re-firing
on store changes).

```tsx
useEffect(() => {
  const panel = searchParams.get('panel');
  const groupId = searchParams.get('groupId');
  const mode = searchParams.get('mode');
  const urlKey = buildUrlKey(panel, groupId, mode);

  // Skip if this URL was set by our Store→URL sync
  if (pendingUrlRef.current === urlKey) {
    pendingUrlRef.current = null;
    return;
  }

  const store = useGroupsStore.getState();

  if (panel === 'create') {
    if (!store.isCreatePanelOpen) openCreatePanel();
    initialUrlProcessed.current = true;
    return;
  }

  if (panel === 'detail' && groupId) {
    const alreadyShowing =
      store.isDetailPanelOpen && store.detailGroup?._id === groupId;

    if (alreadyShowing) {
      // Sync edit mode only
      if (mode === 'edit' && !store.isEditMode) enterEditMode();
      else if (mode !== 'edit' && store.isEditMode) exitEditMode();
      initialUrlProcessed.current = true;
    } else {
      const existing = store.groups.find((g) => g._id === groupId);
      if (existing) {
        openDetailPanel(existing);
        if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
        initialUrlProcessed.current = true;
      } else if (!store.isLoading) {
        // Fetch by ID
        GroupsApi.getGroup(groupId)
          .then((group) => {
            openDetailPanel(group);
            if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
            initialUrlProcessed.current = true;
          })
          .catch(() => {
            initialUrlProcessed.current = true;
            pendingUrlRef.current = buildUrlKey(null, null, null);
            router.replace('/workspace/groups/');
          });
      }
      // If still loading → initialUrlProcessed stays false → Store→URL won't fire
    }
    return;
  }

  // No panel param — close any open panels (only after first load)
  if (initialUrlProcessed.current) {
    if (store.isCreatePanelOpen) closeCreatePanel();
    if (store.isDetailPanelOpen) closeDetailPanel();
  }
  initialUrlProcessed.current = true;
}, [searchParams]);  // Only searchParams as dependency!
```

**Key**: Only `searchParams` is a dependency. Store state is read via `.getState()`.

### 3. Group Resolver Effect (for page reload with groupId)

When the page loads with `?groupId=xxx`, groups may not have loaded yet.
This effect retries once groups arrive:

```tsx
useEffect(() => {
  if (isLoading || groups.length === 0) return;

  const panel = searchParams.get('panel');
  const groupId = searchParams.get('groupId');
  const mode = searchParams.get('mode');

  if (panel !== 'detail' || !groupId) return;
  const store = useGroupsStore.getState();
  if (store.isDetailPanelOpen && store.detailGroup?._id === groupId) return;

  const existing = groups.find((g) => g._id === groupId);
  if (existing) {
    openDetailPanel(existing);
    if (mode === 'edit') setTimeout(() => enterEditMode(), 0);
  } else {
    GroupsApi.getGroup(groupId).then(/* ... */).catch(/* ... */);
  }
}, [groups, isLoading]);
```

### 4. Store → URL Effect

Updates URL when panel state changes from within sidebar components
(e.g., user clicks Edit, Cancel, X, or Delete):

```tsx
useEffect(() => {
  if (!initialUrlProcessed.current) return;

  let targetPanel: string | null = null;
  let targetGroupId: string | null = null;
  let targetMode: string | null = null;

  if (isCreatePanelOpen) {
    targetPanel = 'create';
  } else if (isDetailPanelOpen && detailGroup) {
    targetPanel = 'detail';
    targetGroupId = detailGroup._id;
    if (isEditMode) targetMode = 'edit';
  }

  const currentPanel = searchParams.get('panel');
  const currentGroupId = searchParams.get('groupId');
  const currentMode = searchParams.get('mode');

  if (
    targetPanel !== currentPanel ||
    targetGroupId !== currentGroupId ||
    targetMode !== currentMode
  ) {
    // Set pending key so URL→Store skips this update
    pendingUrlRef.current = buildUrlKey(targetPanel, targetGroupId, targetMode);

    const params = new URLSearchParams();
    if (targetPanel) params.set('panel', targetPanel);
    if (targetGroupId) params.set('groupId', targetGroupId);
    if (targetMode) params.set('mode', targetMode);

    const query = params.toString();
    router.replace(query ? `/workspace/groups/?${query}` : '/workspace/groups/');
  }
}, [isCreatePanelOpen, isDetailPanelOpen, isEditMode, detailGroup, searchParams, router]);
```

### 5. Navigation Helpers

All explicit panel opens use `router.push()` (adds to browser history):

```tsx
const navigateToCreatePanel = useCallback(() => {
  router.push('/workspace/groups/?panel=create');
}, [router]);

const navigateToDetailPanel = useCallback((group: Group) => {
  router.push(`/workspace/groups/?panel=detail&groupId=${group._id}`);
}, [router]);
```

Replace direct store action calls in JSX (CTA buttons, row clicks, action menus).

### 6. Sidebar Components Stay Unchanged

Sidebar components call store close/exit actions directly (`closeCreatePanel()`,
`closeDetailPanel()`, `exitEditMode()`). The Store→URL sync effect in the page
detects the state change and updates the URL automatically.

## Why pendingUrlRef Works

```
Store→URL sets URL:
  1. pendingUrlRef.current = "detail|abc123|edit"
  2. router.replace('?panel=detail&groupId=abc123&mode=edit')

URL changes → URL→Store fires:
  3. Reads params → builds key: "detail|abc123|edit"
  4. Compares with pendingUrlRef.current → MATCH
  5. Clears ref, returns early → NO store action → NO loop
```

Unlike `requestAnimationFrame` (which can fire before React processes the new
`searchParams`), string comparison is deterministic — the URL→Store effect always
sees the exact key that Store→URL set, regardless of timing.

## The initialUrlProcessed Guard

On page load, the URL→Store effect fires before async data (like groups) has loaded.
Without the guard, the Store→URL effect would see no panel is open and clear the URL
before the data resolver even gets a chance to open the panel.

**Critical**: When the URL→Store effect encounters `panel=detail&groupId=xxx` but
groups are still loading (`isLoading=true`), it must **NOT** set `initialUrlProcessed`
to `true`. This keeps the Store→URL effect disabled until the data resolver can
open the panel. Each resolved code path sets `initialUrlProcessed = true` individually.

The guard ensures:
- First render: URL is processed, panels are opened if params exist
- Only AFTER that: absence of params triggers panel close
- Data-dependent panels aren't prematurely cleared while loading

## Checklist for Adding URL State to a New Page

1. `import { useSearchParams, useRouter } from 'next/navigation'`
2. Add `pendingUrlRef`, `initialUrlProcessed` refs and `buildUrlKey` helper
3. Pull store selectors: `isXxxPanelOpen`, `closeXxxPanel`, `openXxxPanel`, etc.
4. Add **URL→Store** effect (deps: `[searchParams]` only)
5. Add **Group Resolver** effect if panel depends on async data (deps: `[data, isLoading]`)
6. Add **Store→URL** effect (deps: store state + `searchParams` + `router`)
7. Create `navigateToXxxPanel` helpers with `router.push()`
8. Replace direct store calls in JSX with navigation helpers
9. Add URL scheme to this document

## Pages Currently Implementing This Pattern

- `/workspace/groups` — Create, View, Edit group panels
- `/workspace/users` — Invite users panel
- `/knowledge-base` — View mode, node selection (`view`, `nodeType`, `nodeId`)
- `/chat` — Conversation selection (`conversationId`)

## Important: Trailing Slashes

`trailingSlash: true` is set in `next.config.mjs`. All `router.push()` / `router.replace()` URLs **must** include a trailing slash before the query string:

```tsx
// ✅ Correct
router.push('/workspace/groups/?panel=create');
router.replace('/workspace/groups/');

// ❌ Wrong — causes a redirect blink
router.push('/workspace/groups?panel=create');
router.replace('/workspace/groups');
```