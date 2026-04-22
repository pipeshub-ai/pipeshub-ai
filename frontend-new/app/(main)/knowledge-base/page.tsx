'use client';

import React, { useEffect, useCallback, useState, useMemo, useRef, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex, Box } from '@radix-ui/themes';
import { ServiceGate } from '@/app/components/ui/service-gate';
import {
  // Legacy components (for collections mode)
  KbDataTable,
  MoveFolderSidebar,
  CreateFolderDialog,
  UploadDataSidebar,
  ReplaceFileDialog,
  // KB components (mode-aware)
  Header,
  FilterBar,
  SearchBar,
  // Selection action bar
  SelectionActionBar,
  BulkDeleteConfirmationDialog,
  DeleteConfirmationDialog,
  FolderDetailsSidebar,
  ChatWidgetWrapper,
} from './components';
import type { UploadFileItem } from './components';
import { useUploadStore, generateUploadId } from '@/lib/store/upload-store';
import { applyKnowledgeBaseUploadBatchResult } from './upload-batch-result';
import { KnowledgeBaseApi, KnowledgeHubApi, type FileMetadata } from './api';
// import KnowledgeBaseSidebar from './sidebar';
import { useKnowledgeBaseStore, DEFAULT_PAGE_SIZE } from './store';
import type {
  KnowledgeBaseItem,
  FolderTreeNode,
  NodeType,
  EnhancedFolderTreeNode,
  PageViewMode,
  AllRecordItem,
  KnowledgeHubNode,
  RecordDetailsResponse,
  Breadcrumb,
} from './types';
import { categorizeNodes, mergeChildrenIntoTree, categorizeNode } from './utils/tree-builder';
import {
  getSourceDisplay,
  buildKbLookup,
  applyClientSideFilters,
} from './utils/all-records-transformer';
import { buildFilterParams, buildAllRecordsFilterParams } from './utils';
import { ShareSidebar } from '@/app/components/share';
import type { SharedAvatarMember } from '@/app/components/share';
import { createKBShareAdapter } from './share-adapter';
import {
  serializeCollectionsParams,
  serializeAllRecordsParams,
  parseCollectionsParams,
  parseAllRecordsParams,
  buildFilterUrl,
  buildNavUrl as buildNavUrlFn,
} from './url-params';
import { getIsAllRecordsMode } from './utils/nav';
import { refreshKbTree } from './utils/refresh-kb-tree';
import { toast } from '@/lib/store/toast-store';
import { FilePreviewSidebar, FilePreviewFullscreen } from '@/app/components/file-preview';
import { isPresentationFile, isDocxFile } from '@/app/components/file-preview/utils';
import { useDebouncedSearch } from './hooks/use-debounced-search';

function KnowledgeBasePageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // View mode detection from query params
  const isAllRecordsMode = getIsAllRecordsMode(searchParams);
  const pageViewMode: PageViewMode = isAllRecordsMode ? 'all-records' : 'collections';

  const kbId = searchParams.get('kbId');
  const folderId = searchParams.get('folderId');
  const nodeId = searchParams.get('nodeId');

  const {
    // Collections mode state
    currentFolderId: storeFolderId,
    setCurrentFolderId,
    expandFolderExclusive,
    categorizedNodes,
    setNodes,
    addNodes,
    setCategorizedNodes,
    cacheNodeChildren,
    clearNodeCacheEntries,
    tableData,
    isLoadingTableData,
    tableDataError,
    selectedNode,
    setTableData,
    setIsLoadingTableData,
    setTableDataError,
    setSelectedNode,
    setCollectionsPagination,
    setCollectionsPage,
    setCollectionsLimit,
    collectionsPagination,
    clearTableData,
    // All Records mode state
    appNodes,
    appChildrenCache,
    allRecordsSidebarSelection,
    allRecordsSearchQuery,
    allRecordsPagination,
    allRecordsTableData,
    isLoadingAllRecordsTable,
    allRecordsTableError,
    searchQuery,
    setSearchQuery,
    setAllRecordsSearchQuery,
    setAppNodes,
    cacheAppChildren,
    setAppLoading,
    setAllRecordsTableData,
    syncAllRecordsPaginationMeta,
    setIsLoadingAllRecordsTable,
    setAllRecordsTableError,
    setLoadingFlatCollections,
    setAllRecordsLimit,
    // Refresh state
    isRefreshing,
    setIsRefreshing,
    // Selection state
    selectedItems,
    selectedRecords,
    clearSelection,
    clearRecordSelection,
    // Bulk actions
    bulkReindexSelected,
    bulkDeleteSelected,
    // View mode
    setCurrentViewMode,
    // Sidebar → Page action bridge
    pendingSidebarAction,
    clearPendingSidebarAction,
    // Filter reset
    clearFilter,
    clearAllRecordsFilter,
  } = useKnowledgeBaseStore();

  // Extract filter and sort separately to create stable references
  const rawFilter = useKnowledgeBaseStore((state) => state.filter);
  const rawSort = useKnowledgeBaseStore((state) => state.sort);

  // Memoize filter and sort to prevent unnecessary re-renders and infinite loops
  // Only recreate when actual property values change, not on every render
  const filter = useMemo(() => rawFilter, [
    rawFilter.recordTypes?.join(','),
    rawFilter.indexingStatus?.join(','),
    rawFilter.origins?.join(','),
    rawFilter.connectorIds?.join(','),
    rawFilter.kbIds?.join(','),
    rawFilter.sizeRanges?.join(','),
    rawFilter.createdAfter,
    rawFilter.createdBefore,
    rawFilter.updatedAfter,
    rawFilter.updatedBefore,
    rawFilter.searchQuery,
  ]);

  const sort = useMemo(() => rawSort, [
    rawSort.field,
    rawSort.order,
  ]);

  // Extract All Records filter and sort separately to create stable references
  const rawAllRecordsFilter = useKnowledgeBaseStore((state) => state.allRecordsFilter);
  const rawAllRecordsSort = useKnowledgeBaseStore((state) => state.allRecordsSort);

  // Stabilize allRecordsFilter - same pattern as collections filter above
  const allRecordsFilter = useMemo(() => rawAllRecordsFilter, [
    rawAllRecordsFilter.nodeTypes?.join(','),
    rawAllRecordsFilter.recordTypes?.join(','),
    rawAllRecordsFilter.indexingStatus?.join(','),
    rawAllRecordsFilter.origins?.join(','),
    rawAllRecordsFilter.collectionIds?.join(','),
    rawAllRecordsFilter.connectorIds?.join(','),
    rawAllRecordsFilter.sizeRanges?.join(','),
    rawAllRecordsFilter.createdAfter,
    rawAllRecordsFilter.createdBefore,
    rawAllRecordsFilter.updatedAfter,
    rawAllRecordsFilter.updatedBefore,
    rawAllRecordsFilter.searchQuery,
  ]);

  const allRecordsSort = useMemo(() => rawAllRecordsSort, [
    rawAllRecordsSort.field,
    rawAllRecordsSort.order,
  ]);

  /**
   * Single source of truth for the current Knowledge Base ID
   * 
   * Priority:
   * 1. Extract from breadcrumbs (most reliable, always present when viewing content)
   * 2. Fall back to URL param kbId (for backward compatibility)
   * 
   * Breadcrumbs are available in both Collections and All Records modes.
   * The KB is at breadcrumbs[1] (index 0 is the root/workspace node).
   */
  const selectedKbId = useMemo(() => {
    const currentTableData = isAllRecordsMode ? allRecordsTableData : tableData;
    const kbIdFromBreadcrumbs = currentTableData?.breadcrumbs?.[1]?.id;
    return kbIdFromBreadcrumbs || kbId;
  }, [isAllRecordsMode, allRecordsTableData, tableData, kbId]);

  // Get table items directly from API response (no client-side filtering)
  const tableItems = useMemo(() => {
    return isAllRecordsMode
      ? allRecordsTableData?.items ?? []
      : tableData?.items ?? [];
  }, [isAllRecordsMode, allRecordsTableData?.items, tableData?.items]);

  // Search bar state
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  // Debounced search queries (300ms delay)
  const debouncedSearchQuery = useDebouncedSearch(searchQuery, 300);
  const debouncedAllRecordsSearchQuery = useDebouncedSearch(allRecordsSearchQuery, 300);

  // ========================================
  // URL ↔ Store Sync for Filters, Sort, Pagination, Search
  // ========================================

  const hasHydratedFromUrl = useRef(false);
  const isFirstUrlSyncRender = useRef(true);

  // Hydration: Parse URL params into store on initial mount
  useEffect(() => {
    if (hasHydratedFromUrl.current) return;
    hasHydratedFromUrl.current = true;

    const store = useKnowledgeBaseStore.getState();

    if (isAllRecordsMode) {
      const parsed = parseAllRecordsParams(searchParams);
      if (Object.keys(parsed.filter).length > 0) store.hydrateAllRecordsFilter(parsed.filter);
      if (parsed.sort.field !== 'updatedAt' || parsed.sort.order !== 'desc') {
        store.setAllRecordsSort(parsed.sort);
      }
      // Set page/limit after sort (sort resets page to 1)
      if (parsed.limit !== 50) store.setAllRecordsLimit(parsed.limit);
      if (parsed.page !== 1) store.setAllRecordsPage(parsed.page);
      if (parsed.searchQuery) {
        store.setAllRecordsSearchQuery(parsed.searchQuery);
        setIsSearchOpen(true);
      }
    } else {
      const parsed = parseCollectionsParams(searchParams);
      if (Object.keys(parsed.filter).length > 0) store.hydrateFilter(parsed.filter);
      if (parsed.sort.field !== 'updatedAt' || parsed.sort.order !== 'desc') {
        store.setSort(parsed.sort);
      }
      // Set page/limit after sort (sort resets page to 1)
      if (parsed.limit !== 50) store.setCollectionsLimit(parsed.limit);
      if (parsed.page !== 1) store.setCollectionsPage(parsed.page);
      if (parsed.searchQuery) {
        store.setSearchQuery(parsed.searchQuery);
        setIsSearchOpen(true);
      }
    }
  }, []);

  // URL sync: Write store state to URL when filter/sort/pagination/search changes
  useEffect(() => {
    // Skip first render (before hydration completes)
    if (isFirstUrlSyncRender.current) {
      isFirstUrlSyncRender.current = false;
      return;
    }
    if (!hasHydratedFromUrl.current) return;

    // Build base navigation params (preserve view, nodeType, nodeId)
    const baseParams: Record<string, string> = {};
    if (isAllRecordsMode) baseParams.view = 'all-records';
    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');
    if (nodeType) baseParams.nodeType = nodeType;
    if (nodeId) baseParams.nodeId = nodeId;

    let filterParams: Record<string, string>;
    if (isAllRecordsMode) {
      filterParams = serializeAllRecordsParams(
        allRecordsFilter,
        allRecordsSort,
        { page: allRecordsPagination.page, limit: allRecordsPagination.limit },
        debouncedAllRecordsSearchQuery
      );
    } else {
      filterParams = serializeCollectionsParams(
        filter,
        sort,
        { page: collectionsPagination.page, limit: collectionsPagination.limit },
        debouncedSearchQuery
      );
    }

    const newUrl = buildFilterUrl(baseParams, filterParams);
    const currentUrl = `/knowledge-base${searchParams.toString() ? `?${searchParams.toString()}` : ''}`;

    if (newUrl !== currentUrl) {
      router.replace(newUrl);
    }
  }, [
    filter, sort, collectionsPagination.page, collectionsPagination.limit, debouncedSearchQuery,
    allRecordsFilter, allRecordsSort, allRecordsPagination.page, allRecordsPagination.limit, debouncedAllRecordsSearchQuery,
    isAllRecordsMode,
  ]);

  // Check if any filters are active (for empty state messaging)
  const hasActiveFilters = useMemo(() => {
    if (isAllRecordsMode) {
      return !!(
        allRecordsFilter.recordTypes?.length ||
        allRecordsFilter.indexingStatus?.length ||
        allRecordsFilter.sizeRanges?.length ||
        allRecordsFilter.createdAfter ||
        allRecordsFilter.createdBefore ||
        allRecordsFilter.updatedAfter ||
        allRecordsFilter.updatedBefore ||
        allRecordsFilter.origins?.length ||
        allRecordsFilter.connectorIds?.length ||
        allRecordsFilter.collectionIds?.length
      );
    }
    return !!(
      filter.recordTypes?.length ||
      filter.indexingStatus?.length ||
      filter.sizeRanges?.length ||
      filter.createdAfter ||
      filter.createdBefore ||
      filter.updatedAfter ||
      filter.updatedBefore ||
      filter.origins?.length ||
      filter.connectorIds?.length ||
      filter.kbIds?.length
    );
  }, [isAllRecordsMode, allRecordsFilter, filter]);

  // Check if search query is active
  const hasSearchQuery = useMemo(() => {
    const currentSearchQuery = isAllRecordsMode ? debouncedAllRecordsSearchQuery : debouncedSearchQuery;
    return !!(currentSearchQuery && currentSearchQuery.trim().length > 0);
  }, [isAllRecordsMode, debouncedAllRecordsSearchQuery, debouncedSearchQuery]);

  // Move folder sidebar state
  const [isMoveDialogOpen, setIsMoveDialogOpen] = useState(false);
  const [itemToMove, setItemToMove] = useState<KnowledgeBaseItem | null>(null);
  const [isMoving, setIsMoving] = useState(false);

  // Create folder dialog state
  const [isCreateFolderDialogOpen, setIsCreateFolderDialogOpen] = useState(false);
  const [isCreatingFolder, setIsCreatingFolder] = useState(false);
  const [createFolderContext, setCreateFolderContext] = useState<{
    type: 'collection' | 'subfolder';
    kbId?: string;
    parentId?: string;
    parentName?: string;
  } | null>(null);

  // Upload sidebar state
  const [isUploadSidebarOpen, setIsUploadSidebarOpen] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  // Replace file dialog state
  const [isReplaceDialogOpen, setIsReplaceDialogOpen] = useState(false);
  const [itemToReplace, setItemToReplace] = useState<KnowledgeHubNode | null>(null);
  const [isReplacing, setIsReplacing] = useState(false);

  // Single delete confirmation dialog state (sidebar)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [itemToDelete, setItemToDelete] = useState<{ id: string; name: string; nodeType?: NodeType; rootKbId?: string } | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Bulk delete confirmation dialog state
  const [isBulkDeleteDialogOpen, setIsBulkDeleteDialogOpen] = useState(false);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);

  // Folder details sidebar state
  const [isFolderDetailsOpen, setIsFolderDetailsOpen] = useState(false);

  // File preview state
  const [previewFile, setPreviewFile] = useState<{
    id: string;
    name: string;
    url: string;
    /** Raw Blob — populated for DOCX so `DocxRenderer` can skip the blob-URL round-trip. */
    blob?: Blob;
    type: string;
    size?: number;
    isLoading?: boolean;
    error?: string;
    recordDetails?: RecordDetailsResponse;
  } | null>(null);
  const [previewMode, setPreviewMode] = useState<'sidebar' | 'fullscreen'>('sidebar');

  // Upload store actions
  const { addItems: addUploadItems, startUpload, completeUpload, failUpload, clearCompleted, bulkUpdateItemStatus } = useUploadStore();

  const prevAllRecordsPageRef = useRef(allRecordsPagination.page);

  // Sync current folder from URL params (Collections mode only).
  // In All Records mode the active node is tracked via nodeId + auto-expansion
  // in the sidebar slot. Skipping here prevents setCurrentFolderId(null) from
  // racing with and overwriting the sidebar slot's setCurrentFolderId(nodeId).
  useEffect(() => {
    if (!isAllRecordsMode) {
      setCurrentFolderId(folderId);
    }
  }, [folderId, isAllRecordsMode, setCurrentFolderId]);

  // Load app nodes for sidebar (used by both Collections and All Records modes)
  useEffect(() => {
    async function fetchAppNodes() {
      try {
        setLoadingFlatCollections(true);
        const response = await KnowledgeHubApi.getRootNodes({
          page: 1,
          limit: 100,
        });

        // Filter to app-type nodes only (getRootNodes can return kb/folder nodes too)
        const appItems = response.items.filter((n) => n.nodeType === 'app');
        // Sort so the KB (Collections) app always appears first
        const kbApps = appItems.filter((n) => n.connector === 'KB');
        const connectorApps = appItems.filter((n) => n.connector !== 'KB');
        setAppNodes([...kbApps, ...connectorApps]);
      } catch (error) {
        console.error('Error fetching app nodes:', error);
        // Use toast rather than overwriting the table error state
        toast.error('Failed to load sidebar', {
          description: 'Could not load app sections. Please refresh the page.',
        });
      } finally {
        setLoadingFlatCollections(false);
      }
    }

    fetchAppNodes();
  }, [setAppNodes, setLoadingFlatCollections]);

  // Fetch children for each app node (lazy loading); for the KB app, also
  // populate nodes + categorizedNodes so the Collections sidebar tree is driven
  // from the same data source.
  useEffect(() => {
    if (appNodes.length === 0) return;

    appNodes.forEach(async (app) => {
      // Use fresh state to avoid stale closure and prevent effect loops
      const { appChildrenCache: freshCache } = useKnowledgeBaseStore.getState();
      if (freshCache.has(app.id)) return;

      // In Collections mode, only prefetch the KB app's children.
      // Non-KB connector apps are fetched lazily when the user enters All Records mode.
      const isKbApp = app.connector === 'KB';
      if (!isKbApp && !isAllRecordsMode) return;

      setAppLoading(app.id, true);
      try {
        const response = await KnowledgeHubApi.getNodeChildren('app', app.id, {
          page: 1,
          limit: 50,
        });
        cacheAppChildren(app.id, response.items);

        // For the KB (Collections) app: populate nodes + categorizedNodes
        // so the Collections sidebar tree renders correctly
        if (isKbApp) {
          setNodes(response.items);
          // KB children have parentId = "apps/<appId>" so pass it as rootParentId
          const categorized = categorizeNodes(response.items, `apps/${app.id}`);
          setCategorizedNodes(categorized);
        }
      } catch (error) {
        console.error(`Error fetching children for app ${app.name}:`, error);
      } finally {
        setAppLoading(app.id, false);
      }
    });
  // appChildrenCache intentionally omitted — read fresh via getState() to avoid infinite loop
  }, [appNodes, isAllRecordsMode, setAppLoading, cacheAppChildren, setNodes, setCategorizedNodes]);

  // All Records mode: Fetch table data (reusable callback)
  const fetchAllRecordsTableData = useCallback(async (nodeType?: string, nodeId?: string) => {
    try {
      setIsLoadingAllRecordsTable(true);
      setAllRecordsTableError(null);

      // Get fresh state from store to avoid stale closure
      const currentState = useKnowledgeBaseStore.getState();
      const currentPagination = currentState.allRecordsPagination;
      const currentAllRecordsSearchQuery = currentState.allRecordsSearchQuery;
      const currentAllRecordsFilter = currentState.allRecordsFilter;
      const currentAllRecordsSort = currentState.allRecordsSort;

      // Build API query params using the new utility
      // Include allRecordsSearchQuery from store to enable API search
      const params = buildAllRecordsFilterParams(
        { ...currentAllRecordsFilter, searchQuery: currentAllRecordsSearchQuery },
        currentAllRecordsSort,
        currentPagination
      );

      console.log('DEBUG::page::fetchAllRecordsTableData::params', {
        searchQuery: currentAllRecordsSearchQuery,
        searchParam: params.q,
        allParams: params,
        nodeType,
        nodeId
      });

      // Check if we have URL parameters for drill-down (nodeType and nodeId)
      let data;
      if (nodeType && nodeId) {
        // Fetch specific folder/node content
        data = await KnowledgeHubApi.loadFolderData(
          nodeType as NodeType,
          nodeId,
          params
        );
      } else {
        // Root level - fetch all records
        data = await KnowledgeHubApi.getAllRootItems(params);
      }
      setAllRecordsTableData(data);
      // Sync derived pagination metadata (totalItems, totalPages, hasNext, hasPrev)
      // without overwriting user-controlled page/limit to avoid triggering effect loops
      if (data.pagination) {
        syncAllRecordsPaginationMeta(data.pagination);
      }
    } catch (error) {
      console.error('Error fetching all records:', error);
      setAllRecordsTableError('Failed to load records');
    } finally {
      setIsLoadingAllRecordsTable(false);
    }
  }, [
    // filter/sort are read from getState() inside the function to avoid stale closure on initial load.
    // They are still in deps so fetchAllRecordsTableData re-memoizes when they change, triggering the fetch effect.
    allRecordsFilter,
    allRecordsSort,
    setIsLoadingAllRecordsTable,
    setAllRecordsTableError,
    setAllRecordsTableData,
    syncAllRecordsPaginationMeta,
  ]);

  // Extract stable primitive values from URL to avoid re-firing effects
  // when router.replace() updates pagination/filter params in the URL.
  const allRecordsNodeType = isAllRecordsMode ? searchParams.get('nodeType') : null;
  const allRecordsNodeId = isAllRecordsMode ? searchParams.get('nodeId') : null;

  // All Records mode: Fetch data on initial load or when navigating to a different node
  useEffect(() => {
    if (!isAllRecordsMode) return;

    if (allRecordsNodeType && allRecordsNodeId) {
      // Drilling down into a specific node
      fetchAllRecordsTableData(allRecordsNodeType, allRecordsNodeId);
    } else {
      // Root level - show all records
      fetchAllRecordsTableData();
    }
  }, [isAllRecordsMode, allRecordsNodeType, allRecordsNodeId]);

  // All Records mode: Re-fetch when filter or sort changes
  const isFirstFilterSortFetch = useRef(true);
  useEffect(() => {
    if (!isAllRecordsMode) return;
    // Skip initial render — the main fetch effect above handles it
    if (isFirstFilterSortFetch.current) {
      isFirstFilterSortFetch.current = false;
      return;
    }
    fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
  }, [allRecordsFilter, allRecordsSort]);

  // All Records mode: Re-fetch when pagination page changes
  useEffect(() => {
    if (isAllRecordsMode) {
      if (allRecordsPagination.page === prevAllRecordsPageRef.current) return;
      prevAllRecordsPageRef.current = allRecordsPagination.page;
      fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
    }
  }, [allRecordsPagination.page]);

  // All Records mode: Re-fetch when pagination limit changes
  useEffect(() => {
    if (isAllRecordsMode && allRecordsPagination.limit !== DEFAULT_PAGE_SIZE) {
      fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
    }
  }, [allRecordsPagination.limit]);

  // All Records mode: Transform API response items to include source display info
  const allRecordsItems = useMemo(() => {
    if (!isAllRecordsMode) return [];

    const items = allRecordsTableData?.items || [];
    // Build KB lookup from cached children of the KB app node
    const kbAppNode = appNodes.find((n) => n.connector === 'KB');
    const kbChildren = (kbAppNode ? appChildrenCache.get(kbAppNode.id) : undefined) ?? [];
    const kbLookup = buildKbLookup(kbChildren);

    // Transform items with source display info
    const transformed = items.map((item) => ({
      ...item,
      ...getSourceDisplay(item, kbLookup),
    }));

    // Apply client-side filters (size, date) since API may not support them
    return applyClientSideFilters(transformed, allRecordsFilter);
  }, [isAllRecordsMode, allRecordsTableData, appNodes, appChildrenCache, allRecordsFilter]);

  // Shared 403 handler: clears stale table state, notifies user, refreshes the
  // sidebar tree, and navigates away from the now-inaccessible collection.
  const handleAccessRevoked = useCallback(async () => {
    clearTableData();
    toast.error('You no longer have access to this collection');
    await refreshKbTree();
    router.push('/knowledge-base');
  }, [clearTableData, refreshKbTree, router]);

  // Fetch table data when node is selected
  const fetchTableData = useCallback(
    async (nodeType: string, nodeId: string) => {
      console.log('DEBUG::page::fetchTableData::start', { nodeType, nodeId });
      setIsLoadingTableData(true);
      setTableDataError(null);

      try {
        // Get the latest state from store to avoid stale closure
        const currentState = useKnowledgeBaseStore.getState();
        const currentPagination = currentState.collectionsPagination;
        const currentSearchQuery = currentState.searchQuery;
        const currentFilter = currentState.filter;
        const currentSort = currentState.sort;

        // Build query params with filter/sort/pagination
        // Include searchQuery from store to enable API search
        const params = buildFilterParams(
          { ...currentFilter, searchQuery: currentSearchQuery },
          currentSort,
          currentPagination
        );

        console.log('DEBUG::page::fetchTableData::params', {
          searchQuery: currentSearchQuery,
          searchParam: params.q,
          allParams: params
        });

        const data = await KnowledgeHubApi.loadFolderData(
          nodeType as NodeType,
          nodeId,
          params
        );

        setTableData(data);

        // Update pagination from response
        if (data.pagination) {
          setCollectionsPagination(data.pagination);
        }
        setSelectedNode({ nodeType, nodeId });

        console.log('DEBUG::page::fetchTableData::success', {
          nodeType,
          nodeId,
          itemsCount: data.items?.length || 0,
          hasBreadcrumbs: !!data.breadcrumbs,
          breadcrumbsLength: data.breadcrumbs?.length || 0,
          breadcrumbs: data.breadcrumbs?.map(b => ({ id: b.id, name: b.name, nodeType: b.nodeType })),
          permissions: data.permissions,
          currentNode: data.currentNode,
        });

        // NOTE: This is a "best-effort" expansion that only succeeds when
        // categorizedNodes is already populated. If it's not ready yet (race
        // condition on direct link navigation), the sidebar slot's dedicated
        // auto-expansion effect (@sidebar/knowledge-base/page.tsx) will pick
        // up the expansion once categorizedNodes arrives.
        if (data.breadcrumbs && data.breadcrumbs.length > 0) {
          // Use fresh state for both cache checks and ID-based KB detection.
          const freshState = useKnowledgeBaseStore.getState();
          const allRootNodes = [
            ...(freshState.categorizedNodes?.shared ?? []),
            ...(freshState.categorizedNodes?.private ?? []),
          ];

          // Detect KB root breadcrumb by ID matching first (reliable), then
          // fall back to nodeType check. The API may return 'folder' nodeType
          // for all breadcrumbs including the KB collection root.
          const kbBreadcrumb = data.breadcrumbs.find(
            (b) => allRootNodes.some((n) => n.id === b.id) || b.nodeType === 'kb'
          );
          const kbTreeNode = kbBreadcrumb ? allRootNodes.find((n) => n.id === kbBreadcrumb.id) : null;

          if (kbBreadcrumb) {
            // Set the current folder to the target nodeId so sidebar highlights it
            setCurrentFolderId(nodeId);

            // Expand the KB in the sidebar tree (exclusive: collapse sibling KBs)
            expandFolderExclusive(kbBreadcrumb.id);

            const kbNodeType = (kbTreeNode?.nodeType ?? kbBreadcrumb.nodeType ?? 'kb') as NodeType;

            // Check if KB children are already cached
            if (!freshState.nodeChildrenCache.has(kbBreadcrumb.id)) {
              // Fetch KB children to populate sidebar
              try {
                const kbChildren = await KnowledgeHubApi.getNodeChildren(kbNodeType, kbBreadcrumb.id, {
                  page: 1,
                  limit: 50,
                });
                cacheNodeChildren(kbBreadcrumb.id, kbChildren.items);
                addNodes(kbChildren.items);

                // Update categorized tree with fresh state
                const latestState = useKnowledgeBaseStore.getState();
                if (latestState.categorizedNodes) {
                  const kbNode = latestState.nodes.find(n => n.id === kbBreadcrumb.id);
                  if (kbNode) {
                    const section = categorizeNode(kbNode);
                    const updatedTree = mergeChildrenIntoTree(
                      latestState.categorizedNodes[section],
                      kbBreadcrumb.id,
                      kbChildren.items
                    );
                    setCategorizedNodes({
                      ...latestState.categorizedNodes,
                      [section]: updatedTree,
                    });
                  }
                }
              } catch (error) {
                console.error('Failed to fetch KB children for sidebar expansion', error);
              }
            }

            // Expand each intermediate folder ancestor (between KB root and target)
            const kbIndex = data.breadcrumbs.findIndex((b) => b.id === kbBreadcrumb.id);
            const pathAfterKb = data.breadcrumbs.slice(kbIndex + 1);
            const intermediates = pathAfterKb.filter((b) => b.id !== nodeId);

            for (const breadcrumb of intermediates) {
              if (breadcrumb.nodeType === 'folder' || breadcrumb.nodeType === 'recordGroup') {
                expandFolderExclusive(breadcrumb.id);

                const iterState = useKnowledgeBaseStore.getState();

                if (!iterState.nodeChildrenCache.has(breadcrumb.id)) {
                  try {
                    const folderChildren = await KnowledgeHubApi.getNodeChildren(
                      breadcrumb.nodeType as NodeType,
                      breadcrumb.id,
                      { page: 1, limit: 50 }
                    );
                    cacheNodeChildren(breadcrumb.id, folderChildren.items);
                    addNodes(folderChildren.items);

                    const mergeState = useKnowledgeBaseStore.getState();
                    if (mergeState.categorizedNodes) {
                      const parentNode = mergeState.nodes.find(n => n.id === breadcrumb.id);
                      if (parentNode) {
                        const section = categorizeNode(parentNode);
                        const updatedTree = mergeChildrenIntoTree(
                          mergeState.categorizedNodes[section],
                          breadcrumb.id,
                          folderChildren.items
                        );
                        setCategorizedNodes({
                          ...mergeState.categorizedNodes,
                          [section]: updatedTree,
                        });
                      }
                    }
                  } catch (error) {
                    console.error('Failed to fetch folder children for sidebar expansion', error);
                  }
                }
              }
            }
          }
        }

      } catch (error) {
        // ProcessedError from apiClient interceptor has statusCode (not response.status)
        const status = (error as { statusCode?: number })?.statusCode;
        if (status === 403) {
          await handleAccessRevoked();
        } else {
          console.error('Failed to fetch table data:', error);
          setTableDataError('Failed to load items. Please try again.');
          setTableData(null);
        }
      } finally {
        setIsLoadingTableData(false);
      }
    },
    // Pagination/filter/sort read via getState() — keep this callback stable so the searchParams
    // effect is the only collections refetch trigger (avoids double fetch with page/limit effects).
    [
      setTableData,
      setIsLoadingTableData,
      setTableDataError,
      setSelectedNode,
      setCollectionsPagination,
      setCurrentFolderId,
      expandFolderExclusive,
      cacheNodeChildren,
      addNodes,
      setCategorizedNodes,
      handleAccessRevoked,
    ]
  );

  // Helper to build navigation URLs that preserve view mode
  const buildNavUrl = useCallback(
    (params: Record<string, string>) =>
      buildNavUrlFn(params, isAllRecordsMode, debouncedSearchQuery, debouncedAllRecordsSearchQuery),
    [isAllRecordsMode, debouncedSearchQuery, debouncedAllRecordsSearchQuery]
  );

  // Sync with URL params and fetch data
  // Sync with URL params and fetch data.
  // When filters/sort/pagination change, the URL sync effect calls router.replace,
  // which updates searchParams and triggers this effect to refetch with latest store values.
  useEffect(() => {
    // All Records mode has its own fetch effect below — skip here.
    if (isAllRecordsMode) return;

    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');

    if (nodeType && nodeId) {
      fetchTableData(nodeType, nodeId);
    } else {
      // No node selected, clear table
      clearTableData();
    }
  }, [isAllRecordsMode, searchParams, fetchTableData, clearTableData]);

  // Collections mode: Fetch table data when debounced search query changes
  // This effect runs when the user types in the search bar and the debounced value updates
  useEffect(() => {
    // Skip if in All Records mode or no node selected
    if (isAllRecordsMode || !selectedNode) return;

    // Skip on initial mount/load when debouncedSearchQuery is empty
    // The URL effect already handles the initial data fetch
    if (!debouncedSearchQuery) return;

    // Only fetch if we have a selected node to search within
    fetchTableData(selectedNode.nodeType, selectedNode.nodeId);
  }, [debouncedSearchQuery, isAllRecordsMode, selectedNode?.nodeType, selectedNode?.nodeId]);

  // All Records mode: Fetch table data when debounced search query changes
  // This effect runs when the user types in the search bar and the debounced value updates
  useEffect(() => {
    // Skip if not in All Records mode
    if (!isAllRecordsMode) return;

    // Skip on initial mount/load when debouncedAllRecordsSearchQuery is empty
    // The existing effect (line 316-319) already handles the initial data fetch
    if (!debouncedAllRecordsSearchQuery) return;

    fetchAllRecordsTableData();
  }, [debouncedAllRecordsSearchQuery, isAllRecordsMode]);

  // Sync page view mode to Zustand store (for loading states and other store consumers)
  useEffect(() => {
    setCurrentViewMode(pageViewMode);
  }, [pageViewMode, setCurrentViewMode]);

  // Bridge: consume pending sidebar actions (reindex/delete/create-collection) and open corresponding dialogs
  useEffect(() => {
    if (!pendingSidebarAction) return;
    if (pendingSidebarAction.type === 'create-collection') {
      setCreateFolderContext({ type: 'collection' });
      setIsCreateFolderDialogOpen(true);
    } else {
      const { type, nodeId, nodeName, nodeType, rootKbId } = pendingSidebarAction;
      if (type === 'reindex') {
        handleReindexClick({ id: nodeId, name: nodeName, nodeType } as KnowledgeHubNode);
      } else if (type === 'delete') {
        setItemToDelete({ id: nodeId, name: nodeName, nodeType, rootKbId });
        setIsDeleteDialogOpen(true);
      }
    }
    clearPendingSidebarAction();
  }, [pendingSidebarAction, clearPendingSidebarAction]);

  // Clear search when switching between Collections and All Records modes
  // Skip initial mount to avoid clearing URL-hydrated search/pagination values
  const prevPageViewModeRef = useRef(pageViewMode);
  useEffect(() => {
    if (prevPageViewModeRef.current === pageViewMode) return; // skip initial mount
    prevPageViewModeRef.current = pageViewMode;
    setSearchQuery('');
    setAllRecordsSearchQuery('');
    setIsSearchOpen(false);
  }, [pageViewMode, setSearchQuery, setAllRecordsSearchQuery]);

  // Collections: page/limit changes go through URL sync → searchParams; no separate pagination effects (avoids double fetch).

  // Helper function to convert EnhancedFolderTreeNode to FolderTreeNode format for move dialog
  const convertEnhancedToFolderTree = useCallback(
    (nodes: EnhancedFolderTreeNode[], depth = 0, parentId: string | null = null): FolderTreeNode[] => {
      if (!nodes || nodes.length === 0) return [];

      return nodes.map((node) => ({
        id: node.id,
        name: node.name,
        depth,
        parentId,
        isExpanded: false,
        children: node.children && node.children.length > 0
          ? convertEnhancedToFolderTree(node.children as EnhancedFolderTreeNode[], depth + 1, node.id)
          : [],
      }));
    },
    []
  );

  // Build a single collection tree for the move dialog (only the current collection)
  const moveCollectionTree = useMemo((): FolderTreeNode | null => {
    if (!selectedKbId || !categorizedNodes) return null;

    // Find the current collection in shared or private trees
    const allTrees = [...categorizedNodes.shared, ...categorizedNodes.private];
    const collectionNode = allTrees.find((node) => node.id === selectedKbId);
    if (!collectionNode) return null;

    // Convert to FolderTreeNode with collection as root
    const children = collectionNode.children && collectionNode.children.length > 0
      ? convertEnhancedToFolderTree(collectionNode.children as EnhancedFolderTreeNode[], 1, collectionNode.id)
      : [];

    return {
      id: collectionNode.id,
      name: collectionNode.name,
      depth: 0,
      parentId: null,
      isExpanded: true,
      children,
    };
  }, [selectedKbId, categorizedNodes, convertEnhancedToFolderTree]);

  // Handle "Go to Collections" from All Records empty state
  const handleGoToCollection = useCallback(() => {
    // Get fresh state to avoid stale closure issues
    const currentSelection = useKnowledgeBaseStore.getState().allRecordsSidebarSelection;

    if (currentSelection.type !== 'collection') return;

    const collectionId = currentSelection.id;

    // Navigate to collections mode (no view parameter = collections by default)
    const urlParams = new URLSearchParams();
    urlParams.set('nodeType', 'recordGroup');
    urlParams.set('nodeId', collectionId);

    router.push(`/knowledge-base?${urlParams.toString()}`);
  }, [router]);

  // Handle find - opens search bar
  const handleFind = useCallback(() => {
    setIsSearchOpen(true);
  }, []);

  // Handle search close - closes search bar and clears query
  const handleSearchClose = useCallback(() => {
    setIsSearchOpen(false);
    if (isAllRecordsMode) {
      setAllRecordsSearchQuery('');
      // Immediate refetch when clearing search (bypasses debounce)
      fetchAllRecordsTableData();
    } else {
      setSearchQuery('');
      // Immediate refetch when clearing search (bypasses debounce)
      if (selectedNode) {
        fetchTableData(selectedNode.nodeType, selectedNode.nodeId);
      }
    }
  }, [isAllRecordsMode, selectedNode, setAllRecordsSearchQuery, setSearchQuery, fetchAllRecordsTableData, fetchTableData]);

  // Handle search change - updates search query in store
  const handleSearchChange = useCallback(
    (query: string) => {
      if (isAllRecordsMode) {
        setAllRecordsSearchQuery(query);
      } else {
        setSearchQuery(query);
      }
    },
    [isAllRecordsMode, setAllRecordsSearchQuery, setSearchQuery]
  );

  // Refetch main table for current route context (shared by handleRefresh and refreshData)
  const refetchMainTableForCurrentRoute = useCallback(async () => {
    if (isAllRecordsMode) {
      // Preserve drill-down context: pass current nodeType/nodeId from URL
      const nodeType = searchParams.get('nodeType');
      const nodeId = searchParams.get('nodeId');
      if (nodeType && nodeId) {
        await fetchAllRecordsTableData(nodeType, nodeId);
      } else {
        await fetchAllRecordsTableData();
      }
    } else if (selectedNode) {
      await fetchTableData(selectedNode.nodeType, selectedNode.nodeId);
    } else {
      // Fallback: if selectedNode not set (e.g. after failed load), use URL params
      const nodeType = searchParams.get('nodeType');
      const nodeId = searchParams.get('nodeId');
      if (nodeType && nodeId) {
        await fetchTableData(nodeType, nodeId);
      }
    }
  }, [isAllRecordsMode, selectedNode, searchParams, fetchTableData, fetchAllRecordsTableData]);

  // Handle refresh
  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await refetchMainTableForCurrentRoute();
    } finally {
      setIsRefreshing(false);
    }
  }, [refetchMainTableForCurrentRoute, setIsRefreshing]);

  // Refresh orchestrator: Syncs sidebar and content area after mutations (delete, create, etc.)
  const refreshData = useCallback(async () => {
    console.log('🔄 Refreshing data: sidebar + content area');

    // Sequential refresh: rebuild root tree first, then re-expand breadcrumb path
    // This avoids a race between root node rebuild and breadcrumb expansion

    // 1. Clear stale cache for current breadcrumb path so children are refetched
    const currentState = useKnowledgeBaseStore.getState();
    if (currentState.tableData?.breadcrumbs) {
      clearNodeCacheEntries(currentState.tableData.breadcrumbs.map(bc => bc.id));
    }

    // 2. Refetch Collections sidebar via KB app children
    await refreshKbTree();

    // 3. Refetch current content area data (which also re-expands the breadcrumb path)
    await refetchMainTableForCurrentRoute();

    console.log('✅ Data refresh complete');
  }, [refetchMainTableForCurrentRoute]);

  // Handle create folder - context-aware
  const handleCreateFolder = useCallback(() => {
    console.log('🔧 handleCreateFolder - tableData:', {
      hasTableData: !!tableData,
      breadcrumbs: tableData?.breadcrumbs,
      currentNode: tableData?.currentNode,
    });

    if (!tableData || !tableData.breadcrumbs || tableData.breadcrumbs.length < 2) {
      // No KB context - create new collection
      console.log('✨ Creating new collection (no KB context)');
      setCreateFolderContext({ type: 'collection' });
      setIsCreateFolderDialogOpen(true);
    } else {
      // Inside a KB/folder - create folder within it
      const kbId = tableData.breadcrumbs[1].id;
      const currentNode = tableData.currentNode;
      
      // Determine parent ID:
      // - If current node is a folder, use it as parent (nested folder)
      // - If current node is the KB itself, parentId should be null (root folder)
      const parentId = currentNode.nodeType === 'folder' ? currentNode.id : null;

      const context = {
        type: 'subfolder' as const,
        kbId,
        parentId,
        parentName: currentNode.name,
      };

      console.log('📁 Creating folder with context:', context);
      setCreateFolderContext(context);
      setIsCreateFolderDialogOpen(true);
    }
  }, [tableData]);

  // Handle create folder submission
  const handleCreateFolderSubmit = useCallback(
    async (name: string, description: string) => {
      if (!name.trim()) return;

      console.log('💾 handleCreateFolderSubmit - context:', createFolderContext);
      setIsCreatingFolder(true);

      try {
        if (createFolderContext?.type === 'subfolder' && createFolderContext.kbId) {
          console.log('📂 Creating folder in KB:', {
            kbId: createFolderContext.kbId,
            parentId: createFolderContext.parentId,
            name: name.trim(),
          });
          // Create subfolder within existing collection
          await KnowledgeBaseApi.createFolder(
            createFolderContext.kbId,
            name.trim(),
            description.trim(),
            createFolderContext.parentId || null
          );

          // Show success toast
            toast.success('Folder created successfully', {
            description: `"${name.trim()}" has been created`,
          });

          // Close dialog and reset
          setIsCreateFolderDialogOpen(false);
          setIsCreatingFolder(false);
          setCreateFolderContext(null);

          // Refresh both sidebar and table to show new subfolder
          await refreshData();
        } else {
          // Create new collection (existing logic)
          const newCollection = await KnowledgeBaseApi.createKnowledgeBase(
            name.trim(),
            description.trim()
          );

          console.log('Collection created:', newCollection);

          // Refresh Collections sidebar via the KB app children
          try {
            await refreshKbTree();
          } catch (error) {
            console.error('Error refreshing collections sidebar:', error);
          }

          // Navigate to new collection
          router.push(buildNavUrl({ nodeType: 'recordGroup', nodeId: newCollection.id }));

          // Close dialog and reset
          setIsCreateFolderDialogOpen(false);
          setIsCreatingFolder(false);
          setCreateFolderContext(null);
        }

      } catch (error: unknown) {
        console.error('Failed to create folder:', error);
        setIsCreatingFolder(false);

        // Show error toast
        const err = error as { response?: { data?: { message?: string } }; message?: string };
        toast.error('Failed to create folder', {
          description: err?.response?.data?.message || err?.message || 'An error occurred',
        });
        // Keep dialog open so user can retry
      }
    },
    [
      createFolderContext,
      router,
      refreshData,
    ]
  );

  // Handle upload - opens the upload sidebar
  const handleUpload = useCallback(() => {
    setIsUploadSidebarOpen(true);
  }, []);

  // Handle upload save - add items to upload store and start uploading
  const handleUploadSave = useCallback(
    async (items: UploadFileItem[]) => {
      if (!selectedKbId) {
        console.error('Cannot upload: No knowledge base ID found');
        return;
      }

      setIsUploading(true);

      // Determine upload target:
      // - If current node is a folder, upload to that folder
      // - Otherwise, upload to KB root
      const currentTableData = isAllRecordsMode ? allRecordsTableData : tableData;
      const currentNode = currentTableData?.currentNode;
      const newParentId = currentNode?.nodeType === 'folder' ? currentNode.id : null;

      // Expand all upload items (files and folder contents) into individual file entries.
      // Each entry gets a pre-generated store ID so we can track it before hitting the API.
      type FileEntry = {
        storeId: string;
        file: File;
        filePath: string; // path metadata sent to the API (preserves folder hierarchy)
      };

      const fileEntries: FileEntry[] = [];

      for (const item of items) {
        if (item.type === 'file' && item.file) {
          fileEntries.push({
            storeId: generateUploadId(),
            file: item.file,
            filePath: item.file.name,
          });
        } else if (item.type === 'folder' && item.filesWithPaths) {
          // Expand folder: one upload-store entry per file inside the folder
          for (const fwp of item.filesWithPaths) {
            fileEntries.push({
              storeId: generateUploadId(),
              file: fwp.file,
              filePath: fwp.relativePath
                ? `${item.name}/${fwp.relativePath}`
                : `${item.name}/${fwp.file.name}`,
            });
          }
        }
      }

      if (fileEntries.length === 0) {
        setIsUploading(false);
        return;
      }

      // Add all individual file entries to the upload store (one row per file)
      addUploadItems(
        fileEntries.map((entry) => ({
          id: entry.storeId,
          name: entry.file.name,
          type: 'file' as const,
          size: entry.file.size,
          file: entry.file,
          knowledgeBaseId: selectedKbId,
          parentId: newParentId,
        }))
      );

      setIsUploadSidebarOpen(false);
      setIsUploading(false);

      // Mark all files as uploading immediately so the tray shows activity
      fileEntries.forEach((entry) => startUpload(entry.storeId));

      // Upload in batches of BATCH_SIZE so large folders are handled gracefully
      const BATCH_SIZE = 10;
      let anySuccess = false;

      for (let i = 0; i < fileEntries.length; i += BATCH_SIZE) {
        const batch = fileEntries.slice(i, i + BATCH_SIZE);

        try {
          const batchFiles = batch.map((e) => e.file);
          // Always use files_metadata format so folder hierarchy is preserved
          const batchMetadata: FileMetadata[] = batch.map((e) => ({
            file_path: e.filePath,
            last_modified: e.file.lastModified,
          }));

          // Route progress updates to every file in this batch in a single store update
          const batchIds = batch.map((e) => e.storeId);
          const onBatchProgress = (progress: number) => {
            bulkUpdateItemStatus(batchIds, 'uploading', progress);
          };

          let responseData: Record<string, unknown>;
          if (newParentId) {
            responseData = await KnowledgeBaseApi.uploadToFolder(
              selectedKbId,
              newParentId,
              batchFiles,
              batchMetadata,
              onBatchProgress
            );
          } else {
            responseData = await KnowledgeBaseApi.uploadToRoot(
              selectedKbId,
              batchFiles,
              batchMetadata,
              onBatchProgress
            );
          }

          applyKnowledgeBaseUploadBatchResult(
            batch.map((e) => ({ storeId: e.storeId, file: e.file, filePath: e.filePath })),
            responseData,
            completeUpload,
            failUpload
          );

          anySuccess = true;
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : 'Upload failed';
          batch.forEach((entry) => failUpload(entry.storeId, errorMessage));
        }
      }

      // Refresh data if any uploads succeeded
      if (anySuccess) {
        await refreshData();

        // Clear completed items from upload tray after a short delay (so user sees completion)
        setTimeout(() => {
          clearCompleted();
        }, 3000);
      }
    },
    [selectedKbId, isAllRecordsMode, allRecordsTableData, tableData, addUploadItems, startUpload, completeUpload, failUpload, bulkUpdateItemStatus, refreshData, clearCompleted]
  );

  // Handle folder info click
  const handleFolderInfoClick = useCallback(() => {
    setIsFolderDetailsOpen(true);
  }, []);

  // Handle share
  const [isShareSidebarOpen, setIsShareSidebarOpen] = useState(false);
  const [sharedMembers, setSharedMembers] = useState<SharedAvatarMember[]>([]);

  // Create the share adapter for the currently selected KB node
  const shareAdapter = useMemo(() => {
    const nodeId = selectedNode?.nodeId;
    if (!nodeId) return null;
    // Only share root KB collection nodes (recordGroup)
    if (selectedNode?.nodeType !== 'recordGroup') return null;
    return createKBShareAdapter(nodeId);
  }, [selectedNode?.nodeId, selectedNode?.nodeType]);

  // Share controls are owner-only for collections.
  const isSelectedKbOwner = !isAllRecordsMode && tableData?.permissions?.role === 'OWNER';
  const canManageSelectedKbSharing = !!shareAdapter && isSelectedKbOwner;
  const canEditSelectedNode = !isAllRecordsMode && tableData?.permissions?.canEdit !== false;
  const canDeleteSelectedNode = !isAllRecordsMode && tableData?.permissions?.canDelete !== false;

  // Whether the selected KB is in the private section (no shared members to fetch)
  // TODO - consider using a json map ds instead of an array for more efficient lookups as the number of nodes grows
  const isSelectedKbPrivate = useMemo(() => {
    const nodeId = selectedNode?.nodeId;
    if (!nodeId || selectedNode?.nodeType !== 'recordGroup') return false;
    return categorizedNodes?.private.some((n) => n.id === nodeId) ?? false;
  }, [selectedNode?.nodeId, selectedNode?.nodeType, categorizedNodes]);

  const handleShare = useCallback(() => {
    if (!canManageSelectedKbSharing) return;
    setIsShareSidebarOpen(true);
  }, [canManageSelectedKbSharing]);

  useEffect(() => {
    if (!canManageSelectedKbSharing && isShareSidebarOpen) {
      setIsShareSidebarOpen(false);
    }
  }, [canManageSelectedKbSharing, isShareSidebarOpen]);

  // Load shared members whenever the selected KB changes.
  // If we get a 403, access was revoked — navigate the user away.
  useEffect(() => {
    if (!canManageSelectedKbSharing || isSelectedKbPrivate) {
      setSharedMembers([]);
      return;
    }
    shareAdapter.getSharedMembers().then((members) => {
      setSharedMembers(
      members.map((m) => ({ id: m.id, name: m.name, avatarUrl: m.avatarUrl, type: m.type }))
      );
    }).catch(async (error) => {
      setSharedMembers([]);
      // ProcessedError from apiClient interceptor has statusCode (not response.status)
      const status = (error as { statusCode?: number })?.statusCode;
      if (status === 403) {
        await handleAccessRevoked();
      }
    });
  }, [canManageSelectedKbSharing, isSelectedKbPrivate, handleAccessRevoked, shareAdapter]);

  // Handle file preview
  const handlePreviewFile = useCallback(async (item: KnowledgeBaseItem | KnowledgeHubNode) => {
    // Check if item is a KnowledgeHubNode
    const isKnowledgeHubNode = 'nodeType' in item && 'origin' in item;
    
    if (isKnowledgeHubNode) {
      // Only preview record type nodes (files)
      if (item.nodeType !== 'record') {
        return;
      }
      
      try {
        // 1. Show loading state immediately
        setPreviewFile({
          id: item.id,
          name: item.name,
          url: '',
          type: item.mimeType || item.extension || '',
          size: item.sizeInBytes || undefined,
          isLoading: true,
        });
        setPreviewMode('sidebar');

        // 2. Fetch record details and stream file in parallel
        // PPT/PPTX files need server-side conversion to PDF for browser preview
        const streamOptions = isPresentationFile(item.mimeType, item.name) ? { convertTo: 'application/pdf' } : undefined;
        const [recordDetails, blob] = await Promise.all([
          KnowledgeBaseApi.getRecordDetails(item.id),
          KnowledgeBaseApi.streamRecord(item.id, streamOptions),
        ]);

        // 3. For DOCX we hand the Blob straight through to DocxRenderer.
        //    All other renderers still expect a URL.
        const resolvedType = recordDetails.record.mimeType || item.extension || '';
        const isDocx = isDocxFile(resolvedType, item.name);
        const url = isDocx ? '' : URL.createObjectURL(blob);

        // 4. Update state with actual file URL and/or Blob
        setPreviewFile({
          id: item.id,
          name: item.name,
          url,
          blob: isDocx ? blob : undefined,
          type: resolvedType,
          size:
            recordDetails.record.sizeInBytes ??
            recordDetails.record.fileRecord?.sizeInBytes ??
            undefined,
          isLoading: false,
          recordDetails,
        });

      } catch (error) {
        console.error('Failed to load file preview:', error);
        setPreviewFile(prev => prev ? {
          ...prev,
          error: error instanceof Error ? error.message : 'Failed to load file',
          isLoading: false,
        } : null);
      }
    } else {
      // Handle legacy KnowledgeBaseItem format
      if (item.type !== 'file') {
        return;
      }

      try {
        // Same flow for legacy items
        setPreviewFile({
          id: item.id,
          name: item.name,
          url: '',
          type: item.fileType || '',
          size: item.size,
          isLoading: true,
        });
        setPreviewMode('sidebar');

        // PPT/PPTX files need server-side conversion to PDF for browser preview
        const legacyStreamOptions = isPresentationFile(item.fileType, item.name) ? { convertTo: 'pdf' } : undefined;
        const [recordDetails, blob] = await Promise.all([
          KnowledgeBaseApi.getRecordDetails(item.id),
          KnowledgeBaseApi.streamRecord(item.id, legacyStreamOptions),
        ]);

        // DOCX uses the Blob directly; other types stay on URLs.
        const resolvedType = recordDetails.record.mimeType || item.fileType || '';
        const isDocx = isDocxFile(resolvedType, item.name);
        const url = isDocx ? '' : URL.createObjectURL(blob);

        setPreviewFile({
          id: item.id,
          name: item.name,
          url,
          blob: isDocx ? blob : undefined,
          type: resolvedType,
          size:
            recordDetails.record.sizeInBytes ??
            recordDetails.record.fileRecord?.sizeInBytes ??
            undefined,
          isLoading: false,
          recordDetails,
        });
        
      } catch (error) {
        console.error('Failed to load file preview:', error);
        setPreviewFile(prev => prev ? {
          ...prev,
          error: error instanceof Error ? error.message : 'Failed to load file',
          isLoading: false,
        } : null);
      }
    }
  }, []);

  // Handle item click (navigate into folder or open file)
  const handleItemClick = useCallback(
    (item: KnowledgeBaseItem | KnowledgeHubNode) => {
      // Check if item is a KnowledgeHubNode (new API format)
      const isKnowledgeHubNode = 'nodeType' in item && 'origin' in item;

      if (isKnowledgeHubNode) {
        const containerTypes: NodeType[] = ['app', 'folder', 'recordGroup'];
        const isNavigableContainer =
          containerTypes.includes(item.nodeType) ||
          (item.nodeType === 'record' && item.hasChildren);

        if (isNavigableContainer) {
          // Reset filters and search when navigating into a container
          if (isAllRecordsMode) {
            clearAllRecordsFilter();
            setAllRecordsSearchQuery('');
          } else {
            clearFilter();
            setSearchQuery('');
          }
          setIsSearchOpen(false);
          // Navigate into container using the helper to preserve view mode
          router.push(buildNavUrl({ nodeType: item.nodeType, nodeId: item.id }));
        } else if (item.nodeType === 'record') {
          // Open file preview for records
          handlePreviewFile(item);
        }
      } else {
        // Handle legacy KnowledgeBaseItem format
        if (item.type === 'folder') {
          // Reset filters and search when navigating into a folder
          clearFilter();
          setSearchQuery('');
          setIsSearchOpen(false);
          setCurrentFolderId(item.id);
          router.push(buildNavUrl({ kbId: selectedKbId || '', folderId: item.id }));
        } else {
          // Open file preview
          handlePreviewFile(item);
        }
      }
    },
    [selectedKbId, router, setCurrentFolderId, handlePreviewFile, buildNavUrl, isAllRecordsMode, clearFilter, clearAllRecordsFilter, setSearchQuery, setAllRecordsSearchQuery, setIsSearchOpen]
  );



  // Handle rename for items in list/grid views
  const handleRename = useCallback(async (
    item: KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem,
    newName: string
  ) => {

    try {
      const isHub = 'nodeType' in item && 'origin' in item;

      if (isHub) {
        const hubItem = item as KnowledgeHubNode;
        if (hubItem.nodeType === 'folder' || hubItem.nodeType === 'recordGroup') {
          if (!selectedKbId) throw new Error('No collection context for rename');
          await KnowledgeBaseApi.renameNode({
            nodeId: hubItem.id,
            newName,
            nodeType: hubItem.nodeType,
            rootKbId: selectedKbId,
          });
        } else if (hubItem.nodeType === 'record') {
          await KnowledgeBaseApi.renameRecord(hubItem.id, newName);
        }
      } else {
        const legacyItem = item as KnowledgeBaseItem;
        if (legacyItem.type === 'folder') {
          if (!selectedKbId) throw new Error('No collection context for rename');
          await KnowledgeBaseApi.renameFolder(selectedKbId, legacyItem.id, newName);
        } else {
          await KnowledgeBaseApi.renameRecord(legacyItem.id, newName);
        }
      }

      toast.success('Renamed successfully', {
        description: `Renamed to "${newName}"`,
      });

      await refreshData();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error('Failed to rename', {
        description: err?.response?.data?.message || err?.message || 'An error occurred',
      });
      throw error;
    }
  }, [selectedKbId, refreshData]);

  // Handle rename from header breadcrumb
  const handleBreadcrumbRename = useCallback(async (
    nodeId: string,
    nodeType: string,
    newName: string
  ) => {

    try {
      if (nodeType === 'folder' || nodeType === 'recordGroup') {
        if (!selectedKbId) throw new Error('No collection context for rename');
        await KnowledgeBaseApi.renameNode({
          nodeId,
          newName,
          nodeType,
          rootKbId: selectedKbId,
        });
      }

      toast.success('Renamed successfully', {
        description: `Renamed to "${newName}"`,
      });

      await refreshData();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error('Failed to rename', {
        description: err?.response?.data?.message || err?.message || 'An error occurred',
      });
      throw error;
    }
  }, [selectedKbId, refreshData]);

  // Handle breadcrumb click - navigate to the clicked breadcrumb
  const handleBreadcrumbClick = useCallback(
    (breadcrumb: Breadcrumb) => {
      // Reset filters and search when navigating via breadcrumbs
      if (isAllRecordsMode) {
        clearAllRecordsFilter();
        setAllRecordsSearchQuery('');
      } else {
        clearFilter();
        setSearchQuery('');
      }
      setIsSearchOpen(false);

      if (breadcrumb.id === 'all-records-root') {
        router.push(buildNavUrl({}));
        return;
      }
      router.push(buildNavUrl({ nodeType: breadcrumb.nodeType, nodeId: breadcrumb.id }));
    },
    [router, buildNavUrl, isAllRecordsMode, clearFilter, clearAllRecordsFilter, setSearchQuery, setAllRecordsSearchQuery, setIsSearchOpen]
  );

  // Handle reindex - directly reindexes the item with loading/success/error toasts
  const handleReindexClick = useCallback(async (item: KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem) => {

    const toastId = toast.loading('Re-indexing...', {
      description: `Collection ${item.name} is getting re-indexed. This may take a few seconds`,
      icon: 'lap_timer',
    });

    try {
      // Check if this is a folder inside an app (use record-group endpoint)
      const currentTableData = isAllRecordsMode ? allRecordsTableData : tableData;
      const breadcrumbs = currentTableData?.breadcrumbs ?? [];
      const isFolderInsideApp =  (item as KnowledgeHubNode).nodeType === 'recordGroup'
        && breadcrumbs.some(b => b.nodeType === 'app') && item.nodeType !== 'folder';

      if (isFolderInsideApp) {
        await KnowledgeBaseApi.reindexRecordGroup(item.id);
      } else {
        await KnowledgeBaseApi.reindexItem(item.id);
      }

      toast.update(toastId, {
        variant: 'success',
        title: 'Reindexed successfully',
        description: `"${item.name}" has been reindexed`,
      });

      await refreshData();
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      toast.update(toastId, {
        variant: 'error',
        title: 'Re-indexing failed',
        description: err?.response?.data?.message || err?.message || 'An error occurred',
        action: {
          label: 'Try Again',
          icon: 'refresh',
          onClick: () => {
            toast.dismiss(toastId);
            handleReindexClick(item);
          },
        },
      });
    }
  }, [refreshData, isAllRecordsMode, allRecordsTableData, tableData]);

  // Handle move - opens the move folder sidebar
  const handleMoveClick = useCallback((item: KnowledgeBaseItem) => {
    setItemToMove(item);
    setIsMoveDialogOpen(true);
  }, []);

  // Handle move confirmation
  const handleMoveConfirm = useCallback(
    async (newParentId: string) => {
      if (!itemToMove || !selectedKbId) return;

      setIsMoving(true);

      try {
        // Call move API
        await KnowledgeBaseApi.moveItem(
          selectedKbId,
          itemToMove.id,
          newParentId
        );

        // Show success toast
        toast.success('Item moved successfully', {
          description: `"${itemToMove.name}" has been moved`,
        });

        // Close dialog and reset
        setIsMoveDialogOpen(false);
        setItemToMove(null);

        // Refresh both sidebar and table to reflect the move
        await refreshData();

      } catch (error: unknown) {
        console.error('Failed to move item:', error);

        // Show error toast
        const err = error as { response?: { data?: { message?: string } }; message?: string };
        toast.error('Failed to move item', {
          description: err?.response?.data?.message || err?.message || 'An error occurred',
        });
      } finally {
        setIsMoving(false);
      }
    },
    [itemToMove, selectedKbId, refreshData]
  );

  // Handle replace - opens the replace file dialog
  const handleReplaceClick = useCallback((item: KnowledgeHubNode) => {
    setItemToReplace(item);
    setIsReplaceDialogOpen(true);
  }, []);

  // Handle replace confirmation
  const handleReplaceConfirm = useCallback(
    async (item: KnowledgeHubNode, newFile: File) => {
      setIsReplacing(true);
      
      try {
        // Call API to replace the file
        await KnowledgeBaseApi.replaceRecord(
          item.id,
          newFile,
          item.name,
          (progress) => {
            console.log(`Upload progress: ${progress}%`);
          }
        );

        // Show success toast
        toast.success('File replaced successfully', {
          description: `"${item.name}" has been replaced with "${newFile.name}"`,
        });

        // Close dialog
        setIsReplaceDialogOpen(false);
        setItemToReplace(null);

        // Refresh both sidebar and table to show updated file metadata
        await refreshData();
      } catch (error: unknown) {
        console.error('Failed to replace file:', error);
        
        // Show error toast
        const err = error as { response?: { data?: { message?: string } }; message?: string };
        toast.error('Failed to replace file', {
          description: err?.response?.data?.message || err?.message || 'An error occurred',
        });
      } finally {
        setIsReplacing(false);
      }
    },
    [refreshData]
  );

  // Handle download
  const handleDownload = useCallback(async (item: KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem) => {
    try {
      await KnowledgeBaseApi.streamDownloadRecord(item.id, item.name);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error('Failed to download', {
        description: err?.response?.data?.message || err?.message || 'An error occurred',
      });
    }
  }, []);

  // Handle delete
  const handleDelete = useCallback((item: KnowledgeBaseItem) => {
    console.log('Delete item:', item.name);
    // TODO: Implement delete confirmation dialog and API call
  }, []);

  // ========================================
  // Sidebar Action Handlers
  // ========================================

  // Sidebar: Delete confirm handler
  const handleSidebarDeleteConfirm = useCallback(async () => {
    if (!itemToDelete) return;
    setIsDeleting(true);
    try {
      await KnowledgeBaseApi.deleteNode({
        nodeId: itemToDelete.id,
        nodeType: itemToDelete.nodeType,
        rootKbId: itemToDelete.rootKbId,
      });
      toast.success(`"${itemToDelete.name}" deleted successfully`);
      setIsDeleteDialogOpen(false);

      // If we deleted the collection we're currently viewing (or an ancestor), navigate away
      // and only refresh the sidebar tree (skip content fetch for the now-deleted node)
      const currentNodeId = searchParams.get('nodeId');
      const currentBreadcrumbIds = tableData?.breadcrumbs?.map(b => b.id) ?? [];
      const deletedCurrentView =
        itemToDelete.id === currentNodeId ||
        currentBreadcrumbIds.includes(itemToDelete.id);

      setItemToDelete(null);

      if (deletedCurrentView) {
        // Clear selectedNode immediately to prevent stale API calls
        // (e.g. permissions fetch for the now-deleted node)
        clearTableData();
        await refreshKbTree();
        router.push(buildNavUrl({}));
      } else {
        await refreshData();
      }
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error(err?.response?.data?.message || `Failed to delete ${itemToDelete.nodeType === 'folder' ? 'folder' : 'collection'}`);
    } finally {
      setIsDeleting(false);
    }
  }, [itemToDelete, refreshData, refreshKbTree, clearTableData, searchParams, tableData?.breadcrumbs, router, buildNavUrl]);

  // Handle create sub-folder from move dialog
  // ========================================
  // Bulk Selection Actions
  // ========================================

  // Get selected items as array
  const selectedItemsArray = useMemo(() => {
    const currentItems = isAllRecordsMode ? allRecordsItems : tableItems;
    const selectedSet = isAllRecordsMode ? selectedRecords : selectedItems;
    return currentItems.filter(item => selectedSet.has(item.id));
  }, [isAllRecordsMode, allRecordsItems, tableItems, selectedRecords, selectedItems]);

  const selectedCount = isAllRecordsMode ? selectedRecords.size : selectedItems.size;

  // Handle deselect all
  const handleDeselectAll = useCallback(() => {
    if (isAllRecordsMode) {
      clearRecordSelection();
    } else {
      clearSelection();
    }
  }, [isAllRecordsMode, clearRecordSelection, clearSelection]);

  // Handle bulk chat
  const handleBulkChat = useCallback(() => {
    const selectedIds = selectedItemsArray.map(item => item.id);
    router.push(`/chat?recordIds=${selectedIds.join(',')}`);
  }, [selectedItemsArray, router]);

  // Handle bulk reindex
  const handleBulkReindex = useCallback(async () => {
    const items = selectedItemsArray.map(item => ({
      id: item.id,
      name: item.name,
    }));
    await bulkReindexSelected(items, refreshData);
  }, [selectedItemsArray, bulkReindexSelected, refreshData]);

  // Handle bulk delete click (opens dialog)
  const handleBulkDeleteClick = useCallback(() => {
    setIsBulkDeleteDialogOpen(true);
  }, []);

  // Handle bulk delete confirm
  const handleBulkDeleteConfirm = useCallback(async () => {
    setIsBulkDeleting(true);
    try {
      const items = selectedItemsArray.map(item => {
        // Determine node type
        const isKnowledgeHubNodeItem = 'nodeType' in item && 'origin' in item;
        let nodeType: 'kb' | 'folder' | 'record' = 'record';
        if (isKnowledgeHubNodeItem) {
          const hubNode = item as KnowledgeHubNode;
          if (hubNode.nodeType === 'kb') nodeType = 'kb';
          else if (['folder', 'recordGroup'].includes(hubNode.nodeType)) nodeType = 'folder';
        } else if ('type' in item && (item as KnowledgeBaseItem).type === 'folder') {
          nodeType = 'folder';
        }

        return {
          id: item.id,
          name: item.name,
          nodeType,
          kbId: selectedKbId || undefined,
        };
      });

      await bulkDeleteSelected(items, refreshData);
      setIsBulkDeleteDialogOpen(false);
    } finally {
      setIsBulkDeleting(false);
    }
  }, [selectedItemsArray, selectedKbId, bulkDeleteSelected, refreshData]);

  // Derive current title based on mode
  const currentTitle = useMemo(() => {
    // Show "Results" when actively searching
    const activeSearchQuery = isAllRecordsMode ? allRecordsSearchQuery : searchQuery;
    if (activeSearchQuery && activeSearchQuery.trim()) {
      return 'Results';
    }

    if (isAllRecordsMode) {
      if (allRecordsSidebarSelection.type === 'all') {
        return 'All';
      } else if (allRecordsSidebarSelection.type === 'collection') {
        return allRecordsSidebarSelection.name;
      } else if (allRecordsSidebarSelection.type === 'connector') {
        return allRecordsSidebarSelection.itemName || allRecordsSidebarSelection.connectorType;
      }
      return 'All';
    }
    return tableData?.currentNode?.name || 'Collections';
  }, [isAllRecordsMode, allRecordsSidebarSelection, tableData, allRecordsSearchQuery, searchQuery]);

  // All Records mode: Prepend "All Records" root breadcrumb
  const allRecordsBreadcrumbs = useMemo<Breadcrumb[]>(() => {
    const rootCrumb: Breadcrumb = {
      id: 'all-records-root',
      name: 'All Records',
      nodeType: 'all-records',
    };
    const apiBreadcrumbs = allRecordsTableData?.breadcrumbs || [];
    return [rootCrumb, ...apiBreadcrumbs];
  }, [allRecordsTableData?.breadcrumbs]);

  return (
    <Flex style={{ height: '100%', width: '100%' }}>
      {/* Main Content */}
      <Flex
        direction="column"
        style={{
          flex: 1,
          height: '100%',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* KB Header - mode-aware; hidden in collections mode when no node is selected */}
        {(isAllRecordsMode ? (allRecordsBreadcrumbs && allRecordsBreadcrumbs.length > 0) : (nodeId && tableData?.breadcrumbs)) && (
          <>
            <Header
              pageViewMode={pageViewMode}
              breadcrumbs={isAllRecordsMode ? allRecordsBreadcrumbs : tableData?.breadcrumbs?.slice(1)}
              currentTitle={currentTitle}
              onBreadcrumbClick={handleBreadcrumbClick}
              onInfoClick={handleFolderInfoClick}
              onFind={handleFind}
              onRefresh={handleRefresh}
              isSearchActive={isSearchOpen && !!(isAllRecordsMode ? allRecordsSearchQuery : searchQuery)?.trim()}
              // Collections mode only props
              onCreateFolder={handleCreateFolder}
              onUpload={handleUpload}
              onShare={canManageSelectedKbSharing ? handleShare : undefined}
              sharedMembers={sharedMembers}
              onRename={
                !isAllRecordsMode && tableData?.permissions?.canEdit !== false
                  ? handleBreadcrumbRename
                  : undefined
              }
            />

            {/* Search Bar - shown when Find is clicked */}
            {isSearchOpen && (
              <SearchBar
                value={isAllRecordsMode ? allRecordsSearchQuery : searchQuery}
                onChange={handleSearchChange}
                onClose={handleSearchClose}
                placeholder="eg: Sales Docs"
              />
            )}

            {/* KB Filter Bar - mode-aware */}
            <FilterBar pageViewMode={pageViewMode} />
          </>
        )}

        {/* Data Table - shows appropriate items based on mode */}
        <KbDataTable
          items={isAllRecordsMode ? allRecordsItems : tableItems}
          isLoading={isAllRecordsMode ? isLoadingAllRecordsTable : isLoadingTableData}
          isRefreshing={isRefreshing}
          error={isAllRecordsMode ? allRecordsTableError : tableDataError}
          pagination={isAllRecordsMode ? allRecordsTableData?.pagination : tableData?.pagination}
          permissions={isAllRecordsMode ? allRecordsTableData?.permissions : tableData?.permissions}
          currentNodeName={isAllRecordsMode ? currentTitle : tableData?.currentNode?.name}
          pageViewMode={pageViewMode}
          showSourceColumn={isAllRecordsMode}
          hasActiveFilters={hasActiveFilters}
          hasSearchQuery={hasSearchQuery}
          onRefresh={() => { void handleRefresh(); }}
          onPageChange={(page) => {
            if (isAllRecordsMode) {
              useKnowledgeBaseStore.getState().setAllRecordsPage(page);
            } else {
              setCollectionsPage(page);
            }
          }}
          onLimitChange={(limit) => {
            if (isAllRecordsMode) {
              setAllRecordsLimit(limit);
            } else {
              setCollectionsLimit(limit);
            }
          }}
          onItemClick={handleItemClick}
          onPreview={handlePreviewFile}
          onRename={
            canEditSelectedNode
              ? handleRename
              : undefined
          }
          onReindex={handleReindexClick}
          onReplace={canEditSelectedNode ? (item) => handleReplaceClick(item as KnowledgeHubNode) : undefined}
          onMove={canEditSelectedNode ? handleMoveClick : undefined}
          onDelete={canDeleteSelectedNode ? handleDelete : undefined}
          onDownload={handleDownload}
          onCreateFolder={isAllRecordsMode ? undefined : handleCreateFolder}
          onUpload={isAllRecordsMode ? undefined : handleUpload}
          onGoToCollection={handleGoToCollection}
          refreshData={refreshData}
        />

        {/* Selection Action Bar - shows when items are selected */}
        <SelectionActionBar
          selectedCount={selectedCount}
          onDeselectAll={handleDeselectAll}
          onChat={handleBulkChat}
          onReindex={handleBulkReindex}
          onDelete={handleBulkDeleteClick}
          pageViewMode={isAllRecordsMode ? 'all-records' : 'collections'}
        />

        {/* Chat Bar */}
        {selectedCount === 0 &&
        <Box
          style={{ 
            position: 'absolute',
            bottom: 'var(--space-6)',
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 10,
          }}
        >
          <ChatWidgetWrapper
            currentTitle={currentTitle}
            selectedKbId={selectedKbId}
            isAllRecordsMode={isAllRecordsMode}
          />
        </Box>
        }
      </Flex>

      {/* Collections mode only dialogs */}
      {/* Delete Confirmation Dialog (sidebar) */}
      {itemToDelete && (
        <DeleteConfirmationDialog
          open={isDeleteDialogOpen}
          onOpenChange={(open) => {
            setIsDeleteDialogOpen(open);
            if (!open) setItemToDelete(null);
          }}
          onConfirm={handleSidebarDeleteConfirm}
          itemName={itemToDelete.name}
          itemType={itemToDelete.nodeType === 'folder' ? 'folder' : 'KB'}
          isDeleting={isDeleting}
        />
      )}

      {/* Collections-only dialogs */}
      {!isAllRecordsMode && (
        <>
          {/* Move Folder Sidebar */}
          <MoveFolderSidebar
            open={isMoveDialogOpen}
            onOpenChange={setIsMoveDialogOpen}
            currentFolderId={storeFolderId}
            collectionTree={moveCollectionTree}
            itemToMoveId={itemToMove?.id}
            onMove={handleMoveConfirm}
            isMoving={isMoving}
          />

          {/* Create Folder Dialog */}
          <CreateFolderDialog
            open={isCreateFolderDialogOpen}
            onOpenChange={setIsCreateFolderDialogOpen}
            onSubmit={handleCreateFolderSubmit}
            isCreating={isCreatingFolder}
            isCollection={createFolderContext?.type === 'collection'}
            parentFolderName={createFolderContext?.parentName}
          />

          {/* Upload Data Sidebar */}
          <UploadDataSidebar
            open={isUploadSidebarOpen}
            onOpenChange={setIsUploadSidebarOpen}
            onSave={handleUploadSave}
            isSaving={isUploading}
          />

          {/* Replace File Dialog */}
          <ReplaceFileDialog
            open={isReplaceDialogOpen}
            onOpenChange={setIsReplaceDialogOpen}
            item={itemToReplace}
            onReplace={handleReplaceConfirm}
            isReplacing={isReplacing}
          />
        </>
      )}

      {/* File Preview - Sidebar Mode */}
      {previewFile && previewMode === 'sidebar' && (
        <FilePreviewSidebar
          open={true}
          source={isAllRecordsMode ? 'all-records' : 'collections'}
          file={{
            id: previewFile.id,
            name: previewFile.name,
            url: previewFile.url,
            blob: previewFile.blob,
            type: previewFile.type,
            size: previewFile.size,
          }}
          isLoading={previewFile.isLoading}
          error={previewFile.error}
          recordDetails={previewFile.recordDetails}
          onToggleFullscreen={() => setPreviewMode('fullscreen')}
          onOpenChange={(open) => {
            if (!open) {
              // Clean up blob URL (only PDF/image/html/etc. paths allocate one)
              if (previewFile.url && previewFile.url.startsWith('blob:')) {
                URL.revokeObjectURL(previewFile.url);
              }
              setPreviewFile(null);
            }
          }}
        />
      )}

      {/* File Preview - Fullscreen Mode */}
      {previewFile && previewMode === 'fullscreen' && (
        <FilePreviewFullscreen
          source={isAllRecordsMode ? 'all-records' : 'collections'}
          file={{
            id: previewFile.id,
            name: previewFile.name,
            url: previewFile.url,
            blob: previewFile.blob,
            type: previewFile.type,
            size: previewFile.size,
          }}
          isLoading={previewFile.isLoading}
          error={previewFile.error}
          recordDetails={previewFile.recordDetails}
          onClose={() => {
            // Clean up blob URL (only PDF/image/html/etc. paths allocate one)
            if (previewFile.url && previewFile.url.startsWith('blob:')) {
              URL.revokeObjectURL(previewFile.url);
            }
            setPreviewFile(null);
          }}
        />
      )}

      {/* Bulk Delete Confirmation Dialog */}
      <BulkDeleteConfirmationDialog
        open={isBulkDeleteDialogOpen}
        onOpenChange={setIsBulkDeleteDialogOpen}
        onConfirm={handleBulkDeleteConfirm}
        itemCount={selectedCount}
        isDeleting={isBulkDeleting}
      />

      {/* Folder Details Sidebar */}
      <FolderDetailsSidebar
        open={isFolderDetailsOpen}
        onOpenChange={setIsFolderDetailsOpen}
        tableData={isAllRecordsMode ? allRecordsTableData : tableData}
      />

      {/* Share Sidebar */}
      {canManageSelectedKbSharing && shareAdapter && (
        <ShareSidebar
          open={isShareSidebarOpen}
          onOpenChange={setIsShareSidebarOpen}
          adapter={shareAdapter}
          onShareSuccess={() => {
            // Re-fetch permissions to update avatar stack
            shareAdapter.getSharedMembers().then((members) => {
              setSharedMembers(
                members.map((m) => ({
                  id: m.id,
                  name: m.name,
                  avatarUrl: m.avatarUrl,
                  type: m.type,
                }))
              );
            }).catch(() => {
              // Access may have been revoked — clear stale members silently
              setSharedMembers([]);
            });
          }}
        />
      )}
    </Flex>
  );
}

export default function KnowledgeBasePage() {
  return (
    <ServiceGate services={['query', 'connector']}>
      <Suspense>
        <KnowledgeBasePageContent />
      </Suspense>
    </ServiceGate>
  );
}
