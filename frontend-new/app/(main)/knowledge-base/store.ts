'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { enableMapSet } from 'immer';
import { categorizeNode, mergeChildrenIntoTree } from './utils/tree-builder';
import type {
  SidebarHubCursorPaginationMeta,
  SidebarNodeChildrenPaginationMeta,
} from './utils/sidebar-child-pagination-meta';

/**
 * Default page size for KB and all-records pagination. Shared across store
 * initial state and page-level effects so they stay in sync.
 */
export const DEFAULT_PAGE_SIZE = 50;

import type {
  KnowledgeBase,
  KnowledgeBaseItem,
  FolderTreeNode,
  KnowledgeBaseFilter,
  SortConfig,
  ViewMode,
  KnowledgeHubNode,
  NodeType,
  CategorizedNodes,
  KnowledgeHubApiResponse,
  // All Records types
  PageViewMode,
  AllRecordItem,
  Connector,
  EnhancedFolderTreeNode,
  AllRecordsSidebarSelection,
  AllRecordsFilter,
  AllRecordsSortConfig,
  KnowledgeHubListPagination,
  KnowledgeHubApiPaginationSlice,
} from './types';

// Enable Immer support for Map and Set
enableMapSet();

interface KnowledgeBaseState {
  // Data
  knowledgeBases: KnowledgeBase[];
  currentKnowledgeBase: KnowledgeBase | null;
  items: KnowledgeBaseItem[];
  folderTree: FolderTreeNode[];
  selectedItems: Set<string>;

  // Navigation
  currentFolderId: string | null;
  breadcrumbs: { id: string; name: string }[];

  // UI State
  viewMode: ViewMode;
  filter: KnowledgeBaseFilter;
  sort: SortConfig;
  searchQuery: string;
  expandedFolders: Record<string, boolean>;

  // Loading states
  isLoading: boolean;
  isLoadingItems: boolean;
  error: string | null;

  // Node management
  nodes: KnowledgeHubNode[];
  categorizedNodes: CategorizedNodes | null;
  loadingNodeIds: Set<string>;
  nodeChildrenCache: Map<string, KnowledgeHubNode[]>;
  /** Lazy sidebar children under any non-root parent (folder, kb, recordGroup, …); keyed by parent id */
  nodeChildrenPagination: Map<string, SidebarNodeChildrenPaginationMeta>;
  /** Per-parent “load more” in flight for {@link nodeChildrenPagination} */
  loadingNodeChildrenMoreIds: Set<string>;

  // Table data from new endpoint
  tableData: KnowledgeHubApiResponse | null;
  isLoadingTableData: boolean;
  tableDataError: string | null;

  // Currently selected node in sidebar (for table data fetching)
  selectedNode: {
    nodeType: string;
    nodeId: string;
  } | null;

  // Collections mode pagination
  collectionsPagination: KnowledgeHubListPagination;

  // ========================================
  // All Records Mode State
  // ========================================

  // Current page view mode
  currentViewMode: PageViewMode;

  // All Records data
  allRecords: AllRecordItem[]; // DEPRECATED - will be removed
  connectors: Connector[]; // DEPRECATED - will be removed
  appNodes: KnowledgeHubNode[]; // Flat list of app nodes (each becomes own section)
  appChildrenCache: Map<string, KnowledgeHubNode[]>; // Cache for app children
  /** Server pagination for root app list (getNavigationNodes); null before first fetch */
  appRootListPagination: SidebarHubCursorPaginationMeta | null;
  /** Server pagination for each app's direct children (getNodeChildren with parent app) */
  appChildrenPagination: Map<string, SidebarHubCursorPaginationMeta>;
  /** True while a "load more" for root app list is in flight */
  loadingRootAppListMore: boolean;
  /** Nested sidebar trees for non-KB apps (All Records), keyed by app id — mirrors categorizedNodes for KB */
  connectorAppTrees: Map<string, EnhancedFolderTreeNode[]>;
  loadingAppIds: Set<string>; // Track which apps are loading children

  // All Records navigation (for drill-down)
  allRecordsNavigationStack: Array<{
    nodeType: string;
    nodeId: string;
    nodeName: string;
  }>;

  // All Records table data (from API)
  allRecordsTableData: KnowledgeHubApiResponse | null;
  isLoadingAllRecordsTable: boolean;
  allRecordsTableError: string | null;

  // All Records selection
  allRecordsSidebarSelection: AllRecordsSidebarSelection;
  selectedRecords: Set<string>;

  // All Records UI state
  expandedSections: Set<string>; // 'collections', 'slack', 'google-drive', 'jira'
  allRecordsFilter: AllRecordsFilter;
  allRecordsSort: AllRecordsSortConfig;
  allRecordsSearchQuery: string;

  // All Records pagination
  allRecordsPagination: KnowledgeHubListPagination;

  // All Records loading states (deprecated fields)
  isLoadingAllRecords: boolean;
  isLoadingConnectors: boolean;
  isLoadingFlatCollections: boolean;
  allRecordsError: string | null;

  // Refresh state
  isRefreshing: boolean;

  // Delete state
  deletingNodeIds: Set<string>;

  // Sidebar → Page action bridge (for dialogs rendered in page.tsx)
  pendingSidebarAction:
    | { type: 'reindex' | 'delete'; nodeId: string; nodeName: string; nodeType?: NodeType; rootKbId?: string }
    | { type: 'create-collection' }
    | null;

}

interface KnowledgeBaseActions {
  // Data actions
  setKnowledgeBases: (knowledgeBases: KnowledgeBase[]) => void;
  setCurrentKnowledgeBase: (kb: KnowledgeBase | null) => void;
  setItems: (items: KnowledgeBaseItem[]) => void;
  setFolderTree: (tree: FolderTreeNode[]) => void;

  // Selection actions
  selectItem: (id: string) => void;
  deselectItem: (id: string) => void;
  selectAllItems: () => void;
  clearSelection: () => void;
  toggleItemSelection: (id: string) => void;

  // Navigation actions
  setCurrentFolderId: (folderId: string | null) => void;
  setBreadcrumbs: (breadcrumbs: { id: string; name: string }[]) => void;

  // UI actions
  setViewMode: (mode: ViewMode) => void;
  setFilter: (filter: Partial<KnowledgeBaseFilter>) => void;
  hydrateFilter: (filter: KnowledgeBaseFilter) => void;
  clearFilter: () => void;
  setSort: (sort: SortConfig) => void;
  setSearchQuery: (query: string) => void;
  toggleFolderExpanded: (folderId: string) => void;
  expandFolderExclusive: (folderId: string) => void;

  // Loading actions
  setLoading: (loading: boolean) => void;
  setLoadingItems: (loading: boolean) => void;
  setError: (error: string | null) => void;

  // Node actions
  setNodes: (nodes: KnowledgeHubNode[]) => void;
  addNodes: (nodes: KnowledgeHubNode[]) => void;
  setCategorizedNodes: (categorized: CategorizedNodes) => void;
  setNodeLoading: (nodeId: string, loading: boolean) => void;
  cacheNodeChildren: (parentId: string, children: KnowledgeHubNode[]) => void;
  clearNodeCacheEntries: (nodeIds: string[]) => void;
  reMergeCachedChildrenIntoTree: () => void;
  setNodeChildrenPagination: (parentId: string, meta: SidebarNodeChildrenPaginationMeta | null) => void;
  setLoadingNodeChildrenMore: (parentId: string, loading: boolean) => void;

  // Table data actions
  setTableData: (data: KnowledgeHubApiResponse | null) => void;
  setIsLoadingTableData: (loading: boolean) => void;
  setTableDataError: (error: string | null) => void;
  setSelectedNode: (node: { nodeType: string; nodeId: string } | null) => void;
  clearTableData: () => void;

  // Collections pagination actions (same hub cursor model as All Records)
  setCollectionsPagination: (pagination: KnowledgeHubListPagination) => void;
  setCollectionsListUrlCursor: (cursor: string | null) => void;
  setCollectionsLimit: (limit: number) => void;
  syncCollectionsPaginationMeta: (pagination: KnowledgeHubApiPaginationSlice) => void;

  // ========================================
  // All Records Mode Actions
  // ========================================

  // View mode
  setCurrentViewMode: (mode: PageViewMode) => void;

  // All Records data
  setAllRecords: (records: AllRecordItem[]) => void;
  setConnectors: (connectors: Connector[]) => void;
  setAppNodes: (nodes: KnowledgeHubNode[]) => void;
  appendAppNodes: (appNodes: KnowledgeHubNode[]) => void;
  setAppRootListPagination: (p: SidebarHubCursorPaginationMeta | null) => void;
  setAppChildPagination: (appId: string, p: SidebarHubCursorPaginationMeta) => void;
  setLoadingRootAppListMore: (loading: boolean) => void;
  cacheAppChildren: (appId: string, children: KnowledgeHubNode[]) => void;
  setConnectorAppTree: (appId: string, tree: EnhancedFolderTreeNode[]) => void;
  mergeConnectorAppTreeChildren: (
    appId: string,
    parentId: string,
    children: KnowledgeHubNode[],
    effectiveHasChildFolders?: boolean
  ) => void;
  setAppLoading: (appId: string, loading: boolean) => void;

  // All Records selection
  setAllRecordsSidebarSelection: (selection: AllRecordsSidebarSelection) => void;
  selectRecord: (id: string) => void;
  deselectRecord: (id: string) => void;
  selectAllRecords: () => void;
  clearRecordSelection: () => void;
  toggleRecordSelection: (id: string) => void;

  // All Records UI
  toggleSection: (sectionId: string) => void;
  setSectionExpanded: (sectionId: string, expanded: boolean) => void;
  setAllRecordsFilter: (filter: Partial<AllRecordsFilter>) => void;
  hydrateAllRecordsFilter: (filter: AllRecordsFilter) => void;
  clearAllRecordsFilter: () => void;
  setAllRecordsSort: (sort: AllRecordsSortConfig) => void;
  setAllRecordsSearchQuery: (query: string) => void;

  // All Records pagination
  setAllRecordsPagination: (pagination: KnowledgeHubListPagination) => void;
  /** All Records list: opaque cursor in URL / API (null = first page, send filters + limit). */
  setAllRecordsListUrlCursor: (cursor: string | null) => void;
  setAllRecordsLimit: (limit: number) => void;

  // All Records loading
  setLoadingAllRecords: (loading: boolean) => void;
  setLoadingConnectors: (loading: boolean) => void;
  setLoadingFlatCollections: (loading: boolean) => void;
  setAllRecordsError: (error: string | null) => void;

  // All Records navigation stack
  pushNavigation: (node: { nodeType: string; nodeId: string; nodeName: string }) => void;
  popNavigation: () => void;
  clearNavigation: () => void;

  // All Records table data
  setAllRecordsTableData: (data: KnowledgeHubApiResponse | null) => void;
  syncAllRecordsPaginationMeta: (pagination: KnowledgeHubApiPaginationSlice) => void;
  setIsLoadingAllRecordsTable: (loading: boolean) => void;
  setAllRecordsTableError: (error: string | null) => void;

  // Refresh
  setIsRefreshing: (refreshing: boolean) => void;

  // Delete actions
  deleteNode: (nodeId: string, nodeType: 'kb' | 'folder' | 'record', kbId?: string, refreshData?: () => Promise<void>) => Promise<void>;
  setIsDeletingNode: (nodeId: string, deleting: boolean) => void;

  // Sidebar → Page action bridge
  setPendingSidebarAction: (action: { type: 'reindex' | 'delete'; nodeId: string; nodeName: string; nodeType?: NodeType; rootKbId?: string } | { type: 'create-collection' } | null) => void;
  clearPendingSidebarAction: () => void;

  // Bulk actions
  bulkReindexSelected: (
    items: Array<{ id: string; name: string; nodeType?: string }>,
    refreshData?: () => Promise<void>
  ) => Promise<void>;
  bulkDeleteSelected: (
    items: Array<{ id: string; name: string; nodeType: 'kb' | 'folder' | 'record'; kbId?: string }>,
    refreshData?: () => Promise<void>
  ) => Promise<void>;

  // Reset
  reset: () => void;
}

type KnowledgeBaseStore = KnowledgeBaseState & KnowledgeBaseActions;

const initialState: KnowledgeBaseState = {
  // Collections mode state
  knowledgeBases: [],
  currentKnowledgeBase: null,
  items: [],
  folderTree: [],
  selectedItems: new Set(),
  currentFolderId: null,
  breadcrumbs: [],
  viewMode: 'list',
  filter: {},
  sort: { field: 'updatedAt', order: 'desc' }, // Default to updatedAt desc (matches API default)
  searchQuery: '',
  expandedFolders: {},
  isLoading: false,
  isLoadingItems: false,
  error: null,
  nodes: [],
  categorizedNodes: null,
  loadingNodeIds: new Set(),
  nodeChildrenCache: new Map(),
  nodeChildrenPagination: new Map(),
  loadingNodeChildrenMoreIds: new Set(),
  tableData: null,
  isLoadingTableData: false,
  tableDataError: null,
  selectedNode: null,
  collectionsPagination: {
    limit: DEFAULT_PAGE_SIZE,
    totalItems: 0,
    hasNext: false,
    hasPrev: false,
    listUrlCursor: null,
    startIndex: undefined,
    endIndex: undefined,
    nextCursor: null,
    prevCursor: null,
  },

  // All Records mode state
  currentViewMode: 'collections',
  allRecords: [],
  connectors: [],
  appNodes: [],
  appChildrenCache: new Map(),
  appRootListPagination: null,
  appChildrenPagination: new Map(),
  loadingRootAppListMore: false,
  connectorAppTrees: new Map(),
  loadingAppIds: new Set(),
  allRecordsNavigationStack: [],
  allRecordsTableData: null,
  isLoadingAllRecordsTable: false,
  allRecordsTableError: null,
  allRecordsSidebarSelection: { type: 'all' },
  selectedRecords: new Set(),
  expandedSections: new Set(['collections']), // Collections expanded by default
  allRecordsFilter: {},
  allRecordsSort: { field: 'updatedAt', order: 'desc' },
  allRecordsSearchQuery: '',
  allRecordsPagination: {
    limit: DEFAULT_PAGE_SIZE,
    totalItems: 0,
    hasNext: false,
    hasPrev: false,
    listUrlCursor: null,
    startIndex: undefined,
    endIndex: undefined,
    nextCursor: null,
    prevCursor: null,
  },
  isLoadingAllRecords: false,
  isLoadingConnectors: false,
  isLoadingFlatCollections: false,
  allRecordsError: null,

  // Refresh state
  isRefreshing: false,

  // Delete state
  deletingNodeIds: new Set(),

  // Sidebar → Page action bridge
  pendingSidebarAction: null,
};

export const useKnowledgeBaseStore = create<KnowledgeBaseStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setKnowledgeBases: (knowledgeBases) =>
        set((state) => {
          state.knowledgeBases = knowledgeBases;
        }),

      setCurrentKnowledgeBase: (kb) =>
        set((state) => {
          state.currentKnowledgeBase = kb;
        }),

      setItems: (items) =>
        set((state) => {
          state.items = items;
        }),

      setFolderTree: (tree) =>
        set((state) => {
          state.folderTree = tree;
        }),

      selectItem: (id) =>
        set((state) => {
          state.selectedItems.add(id);
        }),

      deselectItem: (id) =>
        set((state) => {
          state.selectedItems.delete(id);
        }),

      selectAllItems: () =>
        set((state) => {
          state.items.forEach((item) => state.selectedItems.add(item.id));
        }),

      clearSelection: () =>
        set((state) => {
          state.selectedItems.clear();
        }),

      toggleItemSelection: (id) =>
        set((state) => {
          if (state.selectedItems.has(id)) {
            state.selectedItems.delete(id);
          } else {
            state.selectedItems.add(id);
          }
        }),

      setCurrentFolderId: (folderId) =>
        set((state) => {
          state.currentFolderId = folderId;
          state.selectedItems.clear();
        }),

      setBreadcrumbs: (breadcrumbs) =>
        set((state) => {
          state.breadcrumbs = breadcrumbs;
        }),

      setViewMode: (mode) =>
        set((state) => {
          state.viewMode = mode;
        }),

      setFilter: (filter) =>
        set((state) => {
          state.filter = { ...state.filter, ...filter };
          state.collectionsPagination.listUrlCursor = null;
        }),

      hydrateFilter: (filter) =>
        set((state) => {
          state.filter = filter; // Full replacement, no page reset (for URL hydration)
        }),

      clearFilter: () =>
        set((state) => {
          state.filter = {};
          state.collectionsPagination.listUrlCursor = null;
        }),

      setSort: (sort) =>
        set((state) => {
          state.sort = sort;
          state.collectionsPagination.listUrlCursor = null;
        }),

      setSearchQuery: (query) =>
        set((state) => {
          state.searchQuery = query;
          state.collectionsPagination.listUrlCursor = null;
        }),

      toggleFolderExpanded: (folderId) =>
        set((state) => {
          if (state.expandedFolders[folderId]) {
            delete state.expandedFolders[folderId];
          } else {
            state.expandedFolders[folderId] = true;
          }
        }),

      expandFolderExclusive: (folderId) =>
        set((state) => {
          // Helper: collect all descendant IDs from a tree node
          const collectDescendantIds = (nodes: EnhancedFolderTreeNode[]): string[] => {
            const ids: string[] = [];
            for (const node of nodes) {
              ids.push(node.id);
              if (node.children?.length) {
                ids.push(...collectDescendantIds(node.children as EnhancedFolderTreeNode[]));
              }
            }
            return ids;
          };

          // Helper: find the parent's children array (siblings) for a given folderId
          const findSiblings = (
            trees: EnhancedFolderTreeNode[],
            targetId: string
          ): EnhancedFolderTreeNode[] | null => {
            // Check if target is a root node
            for (const node of trees) {
              if (node.id === targetId) return trees;
            }
            // Recurse into children
            for (const node of trees) {
              if (node.children?.length) {
                const found = findSiblings(node.children as EnhancedFolderTreeNode[], targetId);
                if (found) return found;
              }
            }
            return null;
          };

          // Search in both shared and private trees
          const cat = state.categorizedNodes;
          let siblings: EnhancedFolderTreeNode[] | null = null;
          if (cat) {
            siblings = findSiblings(cat.shared, folderId) || findSiblings(cat.private, folderId);
          }

          if (siblings) {
            // Collapse each sibling (except the target) and all their descendants
            for (const sibling of siblings) {
              if (sibling.id !== folderId) {
                delete state.expandedFolders[sibling.id];
                if (sibling.children?.length) {
                  const descendantIds = collectDescendantIds(sibling.children as EnhancedFolderTreeNode[]);
                  for (const id of descendantIds) {
                    delete state.expandedFolders[id];
                  }
                }
              }
            }
          }

          // Expand the target folder
          state.expandedFolders[folderId] = true;
        }),

      setLoading: (loading) =>
        set((state) => {
          state.isLoading = loading;
        }),

      setLoadingItems: (loading) =>
        set((state) => {
          state.isLoadingItems = loading;
        }),

      setError: (error) =>
        set((state) => {
          state.error = error;
        }),

      setNodes: (nodes) =>
        set((state) => {
          state.nodes = nodes;
        }),

      addNodes: (nodes) =>
        set((state) => {
          const existingIds = new Set(state.nodes.map((n) => n.id));
          const newNodes = nodes.filter((n) => !existingIds.has(n.id));
          state.nodes.push(...newNodes);
        }),

      setCategorizedNodes: (categorized) =>
        set((state) => {
          state.categorizedNodes = categorized;
        }),

      setNodeLoading: (nodeId, loading) =>
        set((state) => {
          if (loading) {
            state.loadingNodeIds.add(nodeId);
          } else {
            state.loadingNodeIds.delete(nodeId);
          }
        }),

      cacheNodeChildren: (parentId, children) =>
        set((state) => {
          state.nodeChildrenCache.set(parentId, children);
        }),

      clearNodeCacheEntries: (nodeIds) =>
        set((state) => {
          for (const id of nodeIds) {
            state.nodeChildrenCache.delete(id);
            state.nodeChildrenPagination.delete(id);
            state.loadingNodeChildrenMoreIds.delete(id);
          }
        }),

      setNodeChildrenPagination: (parentId, meta) =>
        set((state) => {
          if (meta == null) {
            state.nodeChildrenPagination.delete(parentId);
          } else {
            state.nodeChildrenPagination.set(parentId, meta);
          }
        }),

      setLoadingNodeChildrenMore: (parentId, loading) =>
        set((state) => {
          if (loading) {
            state.loadingNodeChildrenMoreIds.add(parentId);
          } else {
            state.loadingNodeChildrenMoreIds.delete(parentId);
          }
        }),

      reMergeCachedChildrenIntoTree: () =>
        set((state) => {
          if (!state.categorizedNodes) return;

          for (const [parentId, cachedChildren] of Array.from(state.nodeChildrenCache.entries())) {
            if (state.expandedFolders[parentId] && cachedChildren.length > 0) {
              const parentNode = state.nodes.find(n => n.id === parentId);
              if (parentNode) {
                const section = categorizeNode(parentNode);
                state.categorizedNodes[section] = mergeChildrenIntoTree(
                  state.categorizedNodes[section],
                  parentId,
                  cachedChildren
                );
              }
            }
          }
        }),

      setTableData: (data) =>
        set((state) => {
          state.tableData = data;
        }),

      setIsLoadingTableData: (loading) =>
        set((state) => {
          state.isLoadingTableData = loading;
        }),

      setTableDataError: (error) =>
        set((state) => {
          state.tableDataError = error;
        }),

      setSelectedNode: (node) =>
        set((state) => {
          state.selectedNode = node;
        }),

      clearTableData: () =>
        set((state) => {
          state.tableData = null;
          state.tableDataError = null;
          state.selectedNode = null;
        }),

      setCollectionsPagination: (pagination) =>
        set((state) => {
          state.collectionsPagination = pagination;
        }),

      setCollectionsListUrlCursor: (cursor) =>
        set((state) => {
          state.collectionsPagination.listUrlCursor = cursor;
        }),

      setCollectionsLimit: (limit) =>
        set((state) => {
          state.collectionsPagination.limit = limit;
          state.collectionsPagination.listUrlCursor = null;
        }),

      syncCollectionsPaginationMeta: (pagination) =>
        set((state) => {
          state.collectionsPagination.totalItems = pagination.totalItems;
          state.collectionsPagination.hasNext = pagination.hasNext;
          state.collectionsPagination.hasPrev = pagination.hasPrev;
          state.collectionsPagination.startIndex = pagination.startIndex;
          state.collectionsPagination.endIndex = pagination.endIndex;
          state.collectionsPagination.nextCursor = pagination.nextCursor ?? null;
          state.collectionsPagination.prevCursor = pagination.prevCursor ?? null;
        }),

      // ========================================
      // All Records Mode Actions
      // ========================================

      setCurrentViewMode: (mode) =>
        set((state) => {
          state.currentViewMode = mode;
        }),

      setAllRecords: (records) =>
        set((state) => {
          state.allRecords = records;
          state.allRecordsPagination.totalItems = records.length;
          state.allRecordsPagination.hasNext = false;
          state.allRecordsPagination.hasPrev = false;
        }),

      setConnectors: (connectors) =>
        set((state) => {
          state.connectors = connectors;
        }),

      setAppNodes: (nodes) =>
        set((state) => {
          state.appNodes = nodes;
        }),

      // Append root apps from cursor “load more” at the end (no re-sort).
      appendAppNodes: (newAppNodes) =>
        set((state) => {
          const existingIds = new Set(state.appNodes.map((n) => n.id));
          const toAdd = newAppNodes.filter((n) => n.nodeType === 'app' && !existingIds.has(n.id));
          if (toAdd.length === 0) return;
          state.appNodes = [...state.appNodes, ...toAdd];
        }),

      setAppRootListPagination: (p) =>
        set((state) => {
          state.appRootListPagination = p;
        }),

      setAppChildPagination: (appId, p) =>
        set((state) => {
          state.appChildrenPagination.set(appId, p);
        }),

      setLoadingRootAppListMore: (loading) =>
        set((state) => {
          state.loadingRootAppListMore = loading;
        }),

      cacheAppChildren: (appId, children) =>
        set((state) => {
          state.appChildrenCache.set(appId, children);
        }),

      setConnectorAppTree: (appId, tree) =>
        set((state) => {
          state.connectorAppTrees.set(appId, tree);
        }),

      mergeConnectorAppTreeChildren: (appId, parentId, children, effectiveHasChildFolders) =>
        set((state) => {
          const existing = state.connectorAppTrees.get(appId);
          if (!existing) return;
          const merged = mergeChildrenIntoTree(existing, parentId, children, effectiveHasChildFolders);
          state.connectorAppTrees.set(appId, merged);
        }),

      setAppLoading: (appId, loading) =>
        set((state) => {
          if (loading) {
            state.loadingAppIds.add(appId);
          } else {
            state.loadingAppIds.delete(appId);
          }
        }),

      setAllRecordsSidebarSelection: (selection) =>
        set((state) => {
          state.allRecordsSidebarSelection = selection;
          state.selectedRecords.clear();
          // URL drilldown uses `explorer`; keep list cursor when switching folder in tree.
          if (selection.type !== 'explorer') {
            state.allRecordsPagination.listUrlCursor = null;
          }
        }),

      selectRecord: (id) =>
        set((state) => {
          state.selectedRecords.add(id);
        }),

      deselectRecord: (id) =>
        set((state) => {
          state.selectedRecords.delete(id);
        }),

      selectAllRecords: () =>
        set((state) => {
          const items = state.allRecordsTableData?.items ?? state.allRecords;
          items.forEach((record) => state.selectedRecords.add(record.id));
        }),

      clearRecordSelection: () =>
        set((state) => {
          state.selectedRecords.clear();
        }),

      toggleRecordSelection: (id) =>
        set((state) => {
          if (state.selectedRecords.has(id)) {
            state.selectedRecords.delete(id);
          } else {
            state.selectedRecords.add(id);
          }
        }),

      toggleSection: (sectionId) =>
        set((state) => {
          if (state.expandedSections.has(sectionId)) {
            state.expandedSections.delete(sectionId);
          } else {
            state.expandedSections.add(sectionId);
          }
        }),

      setSectionExpanded: (sectionId, expanded) =>
        set((state) => {
          if (expanded) {
            state.expandedSections.add(sectionId);
          } else {
            state.expandedSections.delete(sectionId);
          }
        }),

      setAllRecordsFilter: (filter) =>
        set((state) => {
          state.allRecordsFilter = { ...state.allRecordsFilter, ...filter };
          state.allRecordsPagination.listUrlCursor = null;
        }),

      hydrateAllRecordsFilter: (filter) =>
        set((state) => {
          state.allRecordsFilter = filter; // Full replacement, no page reset (for URL hydration)
        }),

      clearAllRecordsFilter: () =>
        set((state) => {
          state.allRecordsFilter = {};
          state.allRecordsPagination.listUrlCursor = null;
        }),

      setAllRecordsSort: (sort) =>
        set((state) => {
          state.allRecordsSort = sort;
          state.allRecordsPagination.listUrlCursor = null;
        }),

      setAllRecordsSearchQuery: (query) =>
        set((state) => {
          state.allRecordsSearchQuery = query;
          state.allRecordsPagination.listUrlCursor = null;
        }),

      setAllRecordsPagination: (pagination) =>
        set((state) => {
          state.allRecordsPagination = pagination;
        }),

      setAllRecordsListUrlCursor: (cursor) =>
        set((state) => {
          state.allRecordsPagination.listUrlCursor = cursor;
        }),

      setAllRecordsLimit: (limit) =>
        set((state) => {
          state.allRecordsPagination.limit = limit;
          state.allRecordsPagination.listUrlCursor = null;
        }),

      setLoadingAllRecords: (loading) =>
        set((state) => {
          state.isLoadingAllRecords = loading;
        }),

      setLoadingConnectors: (loading) =>
        set((state) => {
          state.isLoadingConnectors = loading;
        }),

      setLoadingFlatCollections: (loading) =>
        set((state) => {
          state.isLoadingFlatCollections = loading;
        }),

      setAllRecordsError: (error) =>
        set((state) => {
          state.allRecordsError = error;
        }),

      // All Records navigation stack
      pushNavigation: (node) =>
        set((state) => {
          state.allRecordsNavigationStack.push(node);
        }),

      popNavigation: () =>
        set((state) => {
          state.allRecordsNavigationStack.pop();
        }),

      clearNavigation: () =>
        set((state) => {
          state.allRecordsNavigationStack = [];
        }),

      // All Records table data
      setAllRecordsTableData: (data) =>
        set((state) => {
          state.allRecordsTableData = data;
        }),

      // Sync derived pagination metadata from API (cursor-based or legacy page-based).
      syncAllRecordsPaginationMeta: (pagination) =>
        set((state) => {
          state.allRecordsPagination.totalItems = pagination.totalItems;
          state.allRecordsPagination.hasNext = pagination.hasNext;
          state.allRecordsPagination.hasPrev = pagination.hasPrev;
          state.allRecordsPagination.startIndex = pagination.startIndex;
          state.allRecordsPagination.endIndex = pagination.endIndex;
          state.allRecordsPagination.nextCursor = pagination.nextCursor ?? null;
          state.allRecordsPagination.prevCursor = pagination.prevCursor ?? null;
        }),

      setIsLoadingAllRecordsTable: (loading) =>
        set((state) => {
          state.isLoadingAllRecordsTable = loading;
        }),

      setAllRecordsTableError: (error) =>
        set((state) => {
          state.allRecordsTableError = error;
        }),

      setIsRefreshing: (refreshing) =>
        set((state) => {
          state.isRefreshing = refreshing;
        }),

      deleteNode: async (nodeId, nodeType, kbId, refreshData) => {
        const { KnowledgeBaseApi } = await import('./api');
        const { toast } = await import('@/lib/store/toast-store');

        set((state) => {
          state.deletingNodeIds.add(nodeId);
        });

        try {
          // Call appropriate delete API based on node type
          if (nodeType === 'kb') {
            await KnowledgeBaseApi.deleteKnowledgeBase(nodeId);
          } else if (nodeType === 'folder' && kbId) {
            await KnowledgeBaseApi.deleteFolder(kbId, nodeId);
          } else if (nodeType === 'record') {
            await KnowledgeBaseApi.deleteRecord(nodeId);
          }

          // Remove the node from state
          set((state) => {
            // Remove from table data
            if (state.tableData?.items) {
              state.tableData.items = state.tableData.items.filter((item) => item.id !== nodeId);
            }

            // Remove from all records table data
            if (state.allRecordsTableData?.items) {
              state.allRecordsTableData.items = state.allRecordsTableData.items.filter(
                (item) => item.id !== nodeId
              );
            }

            // Remove from nodes
            state.nodes = state.nodes.filter((node) => node.id !== nodeId);

            // Clear from cache
            state.nodeChildrenCache.delete(nodeId);
            state.nodeChildrenPagination.delete(nodeId);
            state.loadingNodeChildrenMoreIds.delete(nodeId);

            // Clear selection
            state.selectedItems.delete(nodeId);
            state.selectedRecords.delete(nodeId);

            state.deletingNodeIds.delete(nodeId);
          });

          // Show success toast immediately
          toast.success('Deleted successfully', {
            description: `The ${nodeType === 'kb' ? 'knowledge base' : nodeType === 'folder' ? 'collection' : 'file'} has been deleted.`,
          });

          // Orchestrate refresh: Re-fetch sidebar and content data
          // This happens after toast, errors are logged but don't affect user feedback
          if (refreshData) {
            refreshData().catch((refreshError) => {
              console.error('Failed to refresh data after deletion:', refreshError);
            });
          }
        } catch (error: unknown) {
          set((state) => {
            state.deletingNodeIds.delete(nodeId);
          });

          toast.error('Delete failed', {
            description: (error as Error).message || 'Failed to delete. Please try again.',
          });

          throw error;
        }
      },

      setIsDeletingNode: (nodeId, deleting) =>
        set((state) => {
          if (deleting) {
            state.deletingNodeIds.add(nodeId);
          } else {
            state.deletingNodeIds.delete(nodeId);
          }
        }),

      setPendingSidebarAction: (action) =>
        set((state) => {
          state.pendingSidebarAction = action;
        }),

      clearPendingSidebarAction: () =>
        set((state) => {
          state.pendingSidebarAction = null;
        }),

      bulkReindexSelected: async (items, refreshData) => {
        const { KnowledgeBaseApi } = await import('./api');
        const { toast } = await import('@/lib/store/toast-store');

        const toastId = toast.loading(`Re-indexing ${items.length} items...`, {
          icon: 'lap_timer',
        });

        try {
          const results = await KnowledgeBaseApi.bulkReindex(items.map(i => ({ id: i.id, nodeType: i.nodeType })));
          const successCount = results.filter(r => r.status === 'fulfilled').length;
          const failCount = results.filter(r => r.status === 'rejected').length;

          if (failCount === 0) {
            toast.update(toastId, {
              variant: 'success',
              title: `Successfully reindexed ${successCount} items`,
            });
          } else {
            toast.update(toastId, {
              variant: 'warning',
              title: `Reindexed ${successCount} items, ${failCount} failed`,
            });
          }

          // Clear selection after bulk action
          set((state) => {
            state.selectedItems.clear();
            state.selectedRecords.clear();
          });

          if (refreshData) {
            await refreshData();
          }
        } catch (error: unknown) {
          toast.update(toastId, {
            variant: 'error',
            title: 'Bulk reindex failed',
            description: (error as Error).message,
          });
          throw error;
        }
      },

      bulkDeleteSelected: async (items, refreshData) => {
        const { KnowledgeBaseApi } = await import('./api');
        const { toast } = await import('@/lib/store/toast-store');

        // Mark all as deleting
        set((state) => {
          items.forEach(item => state.deletingNodeIds.add(item.id));
        });

        const toastId = toast.loading(`Deleting ${items.length} items...`);

        try {
          const results = await KnowledgeBaseApi.bulkDelete(items);
          const successCount = results.filter(r => r.status === 'fulfilled').length;
          const failCount = results.filter(r => r.status === 'rejected').length;

          if (failCount === 0) {
            toast.update(toastId, {
              variant: 'success',
              title: `Successfully deleted ${successCount} items`,
            });
          } else {
            toast.update(toastId, {
              variant: 'warning',
              title: `Deleted ${successCount} items, ${failCount} failed`,
            });
          }

          // Clear selection and deleting state
          set((state) => {
            items.forEach(item => {
              state.deletingNodeIds.delete(item.id);
              state.selectedItems.delete(item.id);
              state.selectedRecords.delete(item.id);
            });
          });

          if (refreshData) {
            await refreshData();
          }
        } catch (error: unknown) {
          // Clear deleting state on error
          set((state) => {
            items.forEach(item => state.deletingNodeIds.delete(item.id));
          });

          toast.update(toastId, {
            variant: 'error',
            title: 'Bulk delete failed',
            description: (error as Error).message,
          });
          throw error;
        }
      },

      reset: () => set(initialState),
    })),
    { name: 'KnowledgeBaseStore' }
  )
);

// Selectors - Collections mode
export const selectKnowledgeBases = (state: KnowledgeBaseStore) => state.knowledgeBases;
export const selectCurrentKnowledgeBase = (state: KnowledgeBaseStore) => state.currentKnowledgeBase;
export const selectItems = (state: KnowledgeBaseStore) => state.items;
export const selectSelectedItems = (state: KnowledgeBaseStore) => state.selectedItems;
export const selectViewMode = (state: KnowledgeBaseStore) => state.viewMode;
export const selectIsLoading = (state: KnowledgeBaseStore) => state.isLoading;
export const selectFilter = (state: KnowledgeBaseStore) => state.filter;

// Selectors - All Records mode
export const selectCurrentViewMode = (state: KnowledgeBaseStore) => state.currentViewMode;
export const selectAllRecords = (state: KnowledgeBaseStore) => state.allRecords;
export const selectConnectors = (state: KnowledgeBaseStore) => state.connectors;
export const selectAppNodes = (state: KnowledgeBaseStore) => state.appNodes;
export const selectAppChildrenCache = (state: KnowledgeBaseStore) => state.appChildrenCache;
export const selectLoadingAppIds = (state: KnowledgeBaseStore) => state.loadingAppIds;
export const selectAllRecordsSidebarSelection = (state: KnowledgeBaseStore) =>
  state.allRecordsSidebarSelection;
export const selectSelectedRecords = (state: KnowledgeBaseStore) => state.selectedRecords;
export const selectExpandedSections = (state: KnowledgeBaseStore) => state.expandedSections;
export const selectAllRecordsFilter = (state: KnowledgeBaseStore) => state.allRecordsFilter;
export const selectAllRecordsSort = (state: KnowledgeBaseStore) => state.allRecordsSort;
export const selectAllRecordsSearchQuery = (state: KnowledgeBaseStore) =>
  state.allRecordsSearchQuery;
export const selectAllRecordsPagination = (state: KnowledgeBaseStore) =>
  state.allRecordsPagination;
export const selectIsLoadingAllRecords = (state: KnowledgeBaseStore) => state.isLoadingAllRecords;
export const selectIsLoadingConnectors = (state: KnowledgeBaseStore) => state.isLoadingConnectors;
export const selectAllRecordsError = (state: KnowledgeBaseStore) => state.allRecordsError;

// Selectors - Available Filters (from API response)
export const selectAvailableFilters = (state: KnowledgeBaseStore) =>
  state.tableData?.filters?.available;
export const selectAllRecordsAvailableFilters = (state: KnowledgeBaseStore) =>
  state.allRecordsTableData?.filters?.available;
