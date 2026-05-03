# Knowledge Base Sidebar Architecture

## Overview

The KB sidebar can be rendered in two ways:

1. **Inline Rendering** (CURRENT) - Sidebar rendered directly in `page.tsx`
2. **Parallel Route Slot** (MIGRATION TARGET) - Sidebar rendered via `@sidebar/knowledge-base/page.tsx`

## Current State (Inline Rendering)

### Files Involved

- **`app/(main)/knowledge-base/page.tsx`** (lines 1795-2140)
  - Renders `<KnowledgeBaseSidebar>` inline
  - Defines all handler functions
  - Passes ~30 props to sidebar

- **`app/(main)/@sidebar/knowledge-base/page.tsx`**
  - Returns `null` (disabled)
  - Contains commented-out implementation ready to use

### Why Inline?

Handlers for sidebar actions (expand node, select KB, reindex, rename, delete) are currently defined in `page.tsx`. These handlers need to stay in the page because they:

1. Access page-local state (e.g., `setCurrentTitle`, `setSelectedNode`)
2. Make API calls (e.g., `KnowledgeBaseApi.getKnowledgeHubNode`)
3. Trigger navigation (e.g., `router.push`)
4. Show toasts/dialogs (e.g., `setIDeleteDialogOpen`)

Passing 42 handler functions as props through the store is complex and not reactive-friendly.

---

## Migration to @sidebar Slot

### Goal

Move sidebar rendering from `page.tsx` to `@sidebar/knowledge-base/page.tsx` (like Chat does).

### Steps

#### 1. Move Handlers to Store Actions

Currently in `page.tsx` (lines 490-1678):

```tsx
// Example: handleNodeExpand (line 490)
const handleNodeExpand = useCallback(async (nodeId: string, nodeType: EnhancedNodeType) => {
  const expandedFolders = useKnowledgeBaseStore.getState().expandedFolders;
  if (expandedFolders.has(nodeId)) {
    useKnowledgeBaseStore.getState().toggleFolderExpanded(nodeId);
    return;
  }

  useKnowledgeBaseStore.getState().toggleFolderExpanded(nodeId);
  useKnowledgeBaseStore.getState().setNodeLoading(nodeId, true);

  try {
    const response = await KnowledgeBaseApi.getKnowledgeHubNode({ nodeId, nodeType });
    useKnowledgeBaseStore.getState().cacheNodeChildren(nodeId, response.results.children);
  } catch (err) {
    console.error('Failed to expand node:', err);
    showToast('Failed to load folder contents', 'error');
    useKnowledgeBaseStore.getState().toggleFolderExpanded(nodeId);
  } finally {
    useKnowledgeBaseStore.getState().setNodeLoading(nodeId, false);
  }
}, []);
```

**Move to store.ts** as:

```tsx
// In store.ts actions
expandNode: async (nodeId: string, nodeType: EnhancedNodeType) => {
  const state = get();
  if (state.expandedFolders.has(nodeId)) {
    set((draft) => {
      draft.expandedFolders.delete(nodeId);
    });
    return;
  }

  set((draft) => {
    draft.expandedFolders.add(nodeId);
    draft.loadingNodeIds.add(nodeId);
  });

  try {
    const response = await KnowledgeBaseApi.getKnowledgeHubNode({ nodeId, nodeType });
    set((draft) => {
      draft.nodeChildrenCache.set(nodeId, response.results.children);
      draft.loadingNodeIds.delete(nodeId);
    });
  } catch (err) {
    console.error('Failed to expand node:', err);
    showToast('Failed to load folder contents', 'error');
    set((draft) => {
      draft.expandedFolders.delete(nodeId);
      draft.loadingNodeIds.delete(nodeId);
    });
  }
},
```

**Repeat for all 42 handlers**:
- `handleNodeSelect`
- `handleSelectKb`
- `handleAllRecordsSelectAll`
- `handleAllRecordsSelectCollection`
- `handleAllRecordsSelectConnectorItem`
- `handleAllRecordsSelectApp`
- `handleSidebarReindex`
- `handleSidebarRename`
- `handleSidebarDelete`
- etc.

#### 2. Update @sidebar/knowledge-base/page.tsx

Remove `return null;` and uncomment the implementation (lines 14-72).

The sidebar will read directly from store:

```tsx
const {
  currentViewMode,
  categorizedNodes,
  flatCollections,
  appNodes,
  // ... all state
  expandNode, // now a store action
  selectNode, // now a store action
  reindexNode, // now a store action
} = useKnowledgeBaseStore();
```

#### 3. Update page.tsx Rendering

**Comment out** the inline sidebar (lines 1796-1867):

```tsx
/*
<KnowledgeBaseSidebar
  pageViewMode={pageViewMode}
  onBack={handleHome}
  sharedTree={categorizedNodes?.shared}
  privateTree={categorizedNodes?.private}
  onSelectKb={handleSelectKb}
  // ... ~30 props
/>
*/
```

**Uncomment** the simple wrapper (currently at lines 2143-2161):

```tsx
<>
  {/* Main Content — sidebar comes from @sidebar parallel route slot */}
  <Flex
    direction="column"
    style={{
      flex: 1,
      height: '100%',
      overflow: 'hidden',
      position: 'relative',
    }}
  >
    {/* ... all the content (Header, FilterBar, etc.) ... */}
  </Flex>
</>
```

#### 4. Ensure Data Flow

The page should populate the store on mount:

```tsx
useEffect(() => {
  const { 
    setCategorizedNodes, 
    setFlatCollections, 
    setAppNodes 
  } = useKnowledgeBaseStore.getState();

  async function loadSidebarData() {
    try {
      // 1 API call to get sidebar tree
      const response = await KnowledgeBaseApi.getKnowledgeHubTree();
      setCategorizedNodes(response.categorizedNodes);
      setFlatCollections(response.flatCollections);
      setAppNodes(response.appNodes);
    } catch (err) {
      console.error('Failed to load sidebar:', err);
    }
  }

  loadSidebarData();
}, []);
```

---

## Reverting from @sidebar to Inline

If you need to revert:

### 1. In `@sidebar/knowledge-base/page.tsx`

Change line 13 back to:

```tsx
return null;
```

### 2. In `page.tsx`

**Uncomment** the inline sidebar (lines 1796-1867):

```tsx
<Flex style={{ height: '100%', width: '100%' }}>
  <KnowledgeBaseSidebar
    pageViewMode={pageViewMode}
    onBack={handleHome}
    // ... ~30 props
  />
  <Flex direction="column" style={{ flex: 1...
```

**Comment out** the simple wrapper (lines 2143-2161):

```tsx
/*
<>
  <Flex direction="column"...
</>
*/
```

---

## Benefits of @sidebar Slot

1. **Consistency** - Chat already uses `@sidebar/chat/page.tsx`
2. **Separation of Concerns** - Layout provides sidebar shell, page provides content
3. **Better State Management** - All logic in store, less prop drilling
4. **Simpler Page** - Page just populates store, sidebar handles its own rendering

## Current Trade-offs

**Why we haven't migrated yet:**

- 42 handlers need to be moved to store (significant refactor)
- Some handlers have complex dependencies (router, dialogs, page state)
- Inline approach is simpler for now (fewer moving parts)

**When to migrate:**

- When adding new features that benefit from centralized state
- When handlers can be simplified to pure store actions
- When we want consistent architecture across Chat + KB

---

## API Calls

### Current Location

Handlers in `page.tsx` make API calls (example from line 490):

```tsx
const response = await KnowledgeBaseApi.getKnowledgeHubNode({ nodeId, nodeType });
```

### After Migration

API calls move to store actions:

```tsx
// In store.ts
expandNode: async (nodeId, nodeType) => {
  const response = await KnowledgeBaseApi.getKnowledgeHubNode({ nodeId, nodeType });
  set((draft) => {
    draft.nodeChildrenCache.set(nodeId, response.results.children);
  });
}
```

### Initial Data Load

**Current:** No explicit load (data passed as props from page)

**After Migration:** Page calls store action on mount:

```tsx
useEffect(() => {
  useKnowledgeBaseStore.getState().loadSidebarTree();
}, []);
```

Store action:

```tsx
// In store.ts
loadSidebarTree: async () => {
  set((draft) => { draft.isLoadingRootNodes = true; });
  try {
    const response = await KnowledgeBaseApi.getKnowledgeHubTree();
    set((draft) => {
      draft.categorizedNodes = response.categorizedNodes;
      draft.flatCollections = response.flatCollections;
      draft.appNodes = response.appNodes;
      draft.isLoadingRootNodes = false;
    });
  } catch (err) {
    set((draft) => { draft.isLoadingRootNodes = false; });
    showToast('Failed to load sidebar', 'error');
  }
}
```

---

## Summary

- **Current:** Inline sidebar with handlers in `page.tsx` (simpler, faster to implement)
- **Target:** @sidebar slot with handlers in `store.ts` (cleaner architecture, consistent with Chat)
- **Migration:** Move 42 handlers to store actions, update page rendering
- **Revert:** Comment/uncomment blocks in 2 files (takes <1 minute)
