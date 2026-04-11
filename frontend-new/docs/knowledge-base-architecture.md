# Knowledge Base (Collections) Page Architecture

This document explains how the Knowledge Base page works internally, including data structures, state management, and UI interactions.

## Table of Contents
1. [Overview](#overview)
2. [Data Flow](#data-flow)
3. [Folder Tree Structure](#folder-tree-structure)
4. [Sidebar Navigation](#sidebar-navigation)
5. [Opening & Closing Folders](#opening--closing-folders)
6. [Node API Data to UI Mapping](#node-api-data-to-ui-mapping)
7. [State Management](#state-management)
8. [Common Operations](#common-operations)

---

## Overview

The Knowledge Base page (`app/(main)/knowledge-base/page.tsx`) has two modes:
- **Collections Mode**: Hierarchical folder tree with CRUD operations
- **All Records Mode**: Flat view of all records across sources

This document focuses on **Collections Mode** and its folder tree navigation.

---

## Data Flow

```
┌─────────────────┐
│   Backend API   │
│ /api/v1/kb/...  │
└────────┬────────┘
         │
         │ HTTP Request (api.ts)
         ↓
┌─────────────────┐
│   Store (Zustand)│
│   store.ts      │
│  - nodes        │
│  - selectedKb   │
│  - breadcrumbs  │
└────────┬────────┘
         │
         │ React state subscription
         ↓
┌─────────────────┐
│  UI Components  │
│  - Sidebar      │
│  - Header       │
│  - DataTable    │
└─────────────────┘
```

### Key Files:
- **`api.ts`**: API calls to backend
- **`store.ts`**: Zustand state management
- **`page.tsx`**: Main page component (reads query params)
- **`components/sidebar.tsx`**: Tree navigation UI
- **`components/kb-data-table.tsx`**: Table displaying folder contents

---

## Folder Tree Structure

### Node Data Structure

Each node in the tree represents either a **Knowledge Base** (top-level collection) or a **Folder**.

```typescript
interface KnowledgeBase {
  kbId: string;              // Unique ID (UUID)
  name: string;              // Display name
  type: 'WORKSPACE' | 'SHARED' | 'PRIVATE';
  parentId: string | null;   // Parent folder ID (null for root KB)
  isFolder: boolean;         // true for folders, false for files
  childrenCount: number;     // Number of children (for expandable indicator)
  // ... other metadata
}
```

### Tree Hierarchy Example

```
WORKSPACE
├── Project Alpha (kbId: "kb-001")
│   ├── Documents (folderId: "folder-001")
│   │   ├── file1.pdf
│   │   └── file2.docx
│   └── Images (folderId: "folder-002")
│       └── logo.png
└── Project Beta (kbId: "kb-002")
    └── Notes (folderId: "folder-003")

SHARED
└── Team Resources (kbId: "kb-003")
```

### How It's Stored in the API

The backend returns nodes in a **flat list**, not a nested tree:

```json
[
  { "kbId": "kb-001", "name": "Project Alpha", "type": "WORKSPACE", "parentId": null },
  { "kbId": "folder-001", "name": "Documents", "type": "WORKSPACE", "parentId": "kb-001" },
  { "kbId": "folder-002", "name": "Images", "type": "WORKSPACE", "parentId": "kb-001" }
]
```

The **UI transforms** this flat list into a tree structure for display.

---

## Sidebar Navigation

### Sidebar Structure

The sidebar (`components/sidebar.tsx`) has three collapsible sections:

```tsx
<Sidebar>
  <Section title="WORKSPACE">
    {/* User's own collections */}
  </Section>

  <Section title="SHARED">
    {/* Collections shared by others */}
  </Section>

  <Section title="PRIVATE">
    {/* Private collections */}
  </Section>
</Sidebar>
```

### How Nodes Are Grouped

The store filters nodes by their `type` property:

```typescript
// In store.ts
const workspaceKbs = nodes.filter(kb => kb.type === 'WORKSPACE');
const sharedKbs = nodes.filter(kb => kb.type === 'SHARED');
const privateKbs = nodes.filter(kb => kb.type === 'PRIVATE');
```

### Tree Node Component

Each node in the tree is rendered recursively:

```tsx
<TreeNode>
  <Flex align="center" gap="2">
    {/* Expand/collapse icon (if has children) */}
    {node.childrenCount > 0 && (
      <ChevronIcon
        isExpanded={isExpanded}
        onClick={toggleExpand}
      />
    )}

    {/* Folder/file icon */}
    <FolderIcon />

    {/* Node name */}
    <Text>{node.name}</Text>

    {/* Actions menu (3-dot) */}
    <ActionsMenu />
  </Flex>

  {/* Recursively render children if expanded */}
  {isExpanded && children.map(child => (
    <TreeNode node={child} depth={depth + 1} />
  ))}
</TreeNode>
```

---

## Opening & Closing Folders

### Expansion State Management

The store tracks which nodes are expanded using a **Set of node IDs**:

```typescript
// In store.ts
interface KnowledgeBaseStore {
  expandedNodes: Set<string>;  // Set of expanded node IDs

  toggleNodeExpansion: (nodeId: string) => void;
  expandNode: (nodeId: string) => void;
  collapseNode: (nodeId: string) => void;
}
```

### Toggle Logic

```typescript
toggleNodeExpansion: (nodeId) => {
  set((state) => {
    const newExpanded = new Set(state.expandedNodes);

    if (newExpanded.has(nodeId)) {
      newExpanded.delete(nodeId);  // Collapse
    } else {
      newExpanded.add(nodeId);     // Expand
    }

    return { expandedNodes: newExpanded };
  });
}
```

### Lazy Loading Children

When a folder is expanded for the **first time**, the UI fetches its children:

```typescript
const handleExpand = async (nodeId: string) => {
  const isExpanded = expandedNodes.has(nodeId);

  if (!isExpanded) {
    // Expand and fetch children
    expandNode(nodeId);
    await fetchNodeChildren(nodeId);
  } else {
    // Collapse
    collapseNode(nodeId);
  }
};
```

### Visual States

```tsx
// Chevron icon rotates based on expansion state
<MaterialIcon
  icon="chevron_right"
  style={{
    transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
    transition: 'transform 0.2s ease',
  }}
/>
```

---

## Node API Data to UI Mapping

### API Response Structure

When you request a Knowledge Base or Folder's contents:

**Request:**
```
GET /api/v1/knowledgeBase/{kbId}/folder/{folderId}/content
```

**Response:**
```json
{
  "nodes": [
    {
      "kbId": "folder-001",
      "name": "Documents",
      "type": "WORKSPACE",
      "isFolder": true,
      "childrenCount": 5,
      "size": 1024000,
      "createdAt": "2024-01-15T10:30:00Z",
      "updatedAt": "2024-01-20T14:45:00Z"
    }
  ],
  "totalCount": 1
}
```

### UI Transformation

The store processes the API response and adds UI-specific properties:

```typescript
// In api.ts
export const KnowledgeBaseApi = {
  async getNodeContent(kbId: string, folderId?: string) {
    const url = folderId
      ? `${BASE_URL}/${kbId}/folder/${folderId}/content`
      : `${BASE_URL}/${kbId}/content`;

    const { data } = await apiClient.get(url);
    return data;  // Returns { nodes, totalCount }
  }
};

// In page.tsx or component
const response = await KnowledgeBaseApi.getNodeContent(kbId, folderId);

// Store the nodes
setTableData(response.nodes.map(node => ({
  ...node,
  isSelected: false,      // UI-specific
  isHovered: false,       // UI-specific
})));
```

### Data Table Display

The `kb-data-table.tsx` component renders nodes in a table:

```tsx
<DataTable>
  {tableData.map(node => (
    <TableRow key={node.kbId}>
      <TableCell>
        <Flex align="center" gap="2">
          {node.isFolder ? <FolderIcon /> : <FileIcon />}
          <Text>{node.name}</Text>
        </Flex>
      </TableCell>
      <TableCell>{formatFileSize(node.size)}</TableCell>
      <TableCell>{formatDate(node.updatedAt)}</TableCell>
      {/* ... more columns */}
    </TableRow>
  ))}
</DataTable>
```

---

## State Management

### Store Structure (`store.ts`)

```typescript
interface KnowledgeBaseStore {
  // Node data
  nodes: KnowledgeBase[];           // All KB nodes (flat list)
  tableData: KnowledgeHubTableNode[]; // Current folder's contents

  // Navigation state
  selectedKb: string | null;        // Current KB ID
  selectedFolder: string | null;    // Current folder ID
  breadcrumbs: Breadcrumb[];        // Navigation path

  // UI state
  expandedNodes: Set<string>;       // Expanded tree nodes
  selectedItems: Set<string>;       // Selected items in table
  viewMode: 'list' | 'grid';        // View mode toggle

  // Filters & sorting
  filter: FilterState;
  sort: SortState;

  // Actions
  fetchNodes: () => Promise<void>;
  navigateToNode: (kbId: string, folderId?: string) => void;
  toggleNodeExpansion: (nodeId: string) => void;
  // ... more actions
}
```

### How Components Use the Store

```tsx
// In a component
import { useKnowledgeBaseStore } from '../store';

function SidebarComponent() {
  // Subscribe to specific state slices
  const nodes = useKnowledgeBaseStore(state => state.nodes);
  const expandedNodes = useKnowledgeBaseStore(state => state.expandedNodes);
  const toggleExpansion = useKnowledgeBaseStore(state => state.toggleNodeExpansion);

  // Use in UI
  return (
    <TreeNode
      onClick={() => toggleExpansion(node.kbId)}
      isExpanded={expandedNodes.has(node.kbId)}
    />
  );
}
```

### Store Update Flow

```
User clicks folder
       ↓
Component calls: navigateToNode(kbId, folderId)
       ↓
Store action:
  1. Update selectedKb and selectedFolder
  2. Update query params: ?nodeType=kb&nodeId=xxx
  3. Fetch folder contents from API
  4. Update tableData with response
       ↓
Components re-render (subscribed to tableData)
```

---

## Common Operations

### 1. Opening a Folder

**User Action:** Clicks on a folder in the sidebar

**Flow:**
```typescript
// 1. User clicks folder
<TreeNode onClick={() => handleNavigateToFolder(node.kbId)} />

// 2. Store action
const navigateToNode = (kbId: string, folderId?: string) => {
  // Update query params
  router.push(`/knowledge-base?nodeType=${folderId ? 'folder' : 'kb'}&nodeId=${folderId || kbId}`);

  // Fetch contents
  const response = await KnowledgeBaseApi.getNodeContent(kbId, folderId);

  // Update state
  set({
    selectedKb: kbId,
    selectedFolder: folderId || null,
    tableData: response.nodes,
    breadcrumbs: buildBreadcrumbs(kbId, folderId),
  });
};

// 3. Table updates automatically (React re-render)
```

### 2. Expanding/Collapsing Tree Node

**User Action:** Clicks chevron icon next to folder name

**Flow:**
```typescript
// 1. User clicks chevron
<ChevronIcon onClick={(e) => {
  e.stopPropagation();  // Prevent navigation
  handleToggleExpand(node.kbId);
}} />

// 2. Store updates expansion state
const toggleNodeExpansion = (nodeId: string) => {
  set((state) => {
    const newExpanded = new Set(state.expandedNodes);
    if (newExpanded.has(nodeId)) {
      newExpanded.delete(nodeId);
    } else {
      newExpanded.add(nodeId);
    }
    return { expandedNodes: newExpanded };
  });
};

// 3. If expanding for first time, fetch children
if (!expandedNodes.has(nodeId)) {
  const children = await KnowledgeBaseApi.getNodeContent(nodeId);
  // Children are added to the nodes array in store
}

// 4. Sidebar re-renders with expanded/collapsed state
```

### 3. Creating a New Folder

**User Action:** Clicks "New" → "Folder" in header

**Flow:**
```typescript
// 1. Open dialog
const [showCreateDialog, setShowCreateDialog] = useState(false);

// 2. User enters folder name and submits
const handleCreateFolder = async (name: string) => {
  const currentKbId = selectedKb;
  const parentFolderId = selectedFolder;

  // 3. API call
  await KnowledgeBaseApi.createFolder(currentKbId, {
    name,
    parentId: parentFolderId,
  });

  // 4. Refresh current folder contents
  const response = await KnowledgeBaseApi.getNodeContent(currentKbId, parentFolderId);

  // 5. Update table data
  set({ tableData: response.nodes });

  // 6. Show success toast
  toast.success('Folder created successfully');
};
```

### 4. Breadcrumb Navigation

**Display:** `Home > Project Alpha > Documents`

**Data Structure:**
```typescript
interface Breadcrumb {
  kbId: string;
  folderId?: string;
  label: string;
}

// Example:
breadcrumbs = [
  { kbId: 'kb-001', label: 'Project Alpha' },
  { kbId: 'kb-001', folderId: 'folder-001', label: 'Documents' },
];
```

**Building Breadcrumbs:**
```typescript
const buildBreadcrumbs = (kbId: string, folderId?: string): Breadcrumb[] => {
  const crumbs: Breadcrumb[] = [];

  // Add KB
  const kb = nodes.find(n => n.kbId === kbId);
  if (kb) {
    crumbs.push({ kbId, label: kb.name });
  }

  // Add folder path (walk up parent chain)
  if (folderId) {
    let currentFolder = nodes.find(n => n.kbId === folderId);
    const folderPath: Breadcrumb[] = [];

    while (currentFolder) {
      folderPath.unshift({
        kbId,
        folderId: currentFolder.kbId,
        label: currentFolder.name,
      });

      currentFolder = currentFolder.parentId
        ? nodes.find(n => n.kbId === currentFolder.parentId)
        : null;
    }

    crumbs.push(...folderPath);
  }

  return crumbs;
};
```

**Click Handler:**
```tsx
<Breadcrumb onClick={() => navigateToNode(crumb.kbId, crumb.folderId)}>
  {crumb.label}
</Breadcrumb>
```

### 5. Filtering & Sorting Table

**Filter State:**
```typescript
interface FilterState {
  type: 'all' | 'folder' | 'file';
  status: 'all' | 'synced' | 'processing';
  dateRange?: { start: Date; end: Date };
  search?: string;
}
```

**Applying Filters:**
```typescript
// In store
const filteredData = computed((state) => {
  let result = state.tableData;

  // Filter by type
  if (state.filter.type !== 'all') {
    result = result.filter(node =>
      state.filter.type === 'folder' ? node.isFolder : !node.isFolder
    );
  }

  // Filter by search
  if (state.filter.search) {
    result = result.filter(node =>
      node.name.toLowerCase().includes(state.filter.search.toLowerCase())
    );
  }

  // Sort
  result = sortNodes(result, state.sort);

  return result;
});
```

---

## Query Params & URL State

The page uses query params to track navigation state:

**URL Format:**
```
/knowledge-base?nodeType=kb&nodeId=kb-001
/knowledge-base?nodeType=folder&nodeId=folder-001
```

**Reading Query Params:**
```typescript
// In page.tsx
'use client';

import { useSearchParams } from 'next/navigation';

export default function KnowledgeBasePage() {
  const searchParams = useSearchParams();
  const nodeType = searchParams.get('nodeType');  // 'kb' | 'folder'
  const nodeId = searchParams.get('nodeId');      // ID

  useEffect(() => {
    if (nodeType && nodeId) {
      // Load node contents
      const kbId = nodeType === 'kb' ? nodeId : findKbForFolder(nodeId);
      const folderId = nodeType === 'folder' ? nodeId : undefined;

      navigateToNode(kbId, folderId);
    }
  }, [nodeType, nodeId]);

  // ... rest of page
}
```

---

## Visual Examples

### Sidebar Tree Rendering

```
┌─────────────────────────────────┐
│ WORKSPACE                    ▼  │  ← Section (collapsible)
│   ► Project Alpha               │  ← KB (collapsed)
│   ▼ Project Beta                │  ← KB (expanded)
│     ► Documents                 │  ← Folder (collapsed)
│     ▼ Images                    │  ← Folder (expanded)
│       • logo.png                │  ← File
│       • banner.jpg              │  ← File
│                                 │
│ SHARED                       ▼  │
│   ► Team Resources              │
│                                 │
│ PRIVATE                      ▼  │
│   (No collections)              │
└─────────────────────────────────┘
```

### Data Table View

```
┌──────────────────────────────────────────────────────────────┐
│  Name              Type     Size      Updated       Status   │
├──────────────────────────────────────────────────────────────┤
│  📁 Documents      Folder   2.3 MB    2 hours ago   Synced   │
│  📁 Images         Folder   15.8 MB   Yesterday     Synced   │
│  📄 report.pdf     File     1.2 MB    Last week     Synced   │
└──────────────────────────────────────────────────────────────┘
```

---

## Summary

**Key Concepts:**
1. **Flat API data** → **Tree UI structure** (transformed in UI)
2. **Zustand store** manages all state (nodes, navigation, expansion)
3. **Query params** track current location (`?nodeType=kb&nodeId=xxx`)
4. **Lazy loading** fetches children only when needed
5. **Recursive rendering** builds nested tree in sidebar
6. **Breadcrumbs** provide navigation path and quick access

**Data Flow:**
```
API (flat list) → Store (state + actions) → Components (tree UI)
```

**Navigation:**
- Click folder in sidebar → Updates URL → Fetches contents → Updates table
- Click breadcrumb → Navigates to that level
- Click chevron → Expands/collapses node in tree (no navigation)
