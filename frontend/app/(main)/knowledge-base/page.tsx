'use client';

import React, { useEffect, useCallback, useState, useMemo, useRef, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams, useRouter } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
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
  ReindexScopeDialog,
} from './components';
import type { UploadFileItem } from './components';
import { useUploadStore, generateUploadId } from '@/lib/store/upload-store';
import { notifyUploadFailures } from '@/lib/utils/upload-failure-feedback';
import { useUploadLimits } from '@/lib/hooks/use-upload-limits';
import { FileRejectionReason, parseFileRejectionReason } from '@/lib/constants/file-rejection-reason';
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
import {
  categorizeNodes,
  effectiveHasChildrenAfterSidebarExpand,
  mergeChildrenIntoTree,
  categorizeNode,
  buildConnectorAppSidebarTree,
  treeHasNodeWithId,
} from './utils/tree-builder';
import {
  getSourceDisplay,
  buildKbLookup,
  applyClientSideFilters,
  isKbCollectionsHubApp,
} from './utils/all-records-transformer';
import { buildFilterParams, buildAllRecordsFilterParams, hasKnowledgeHubFlatteningFilters } from './utils';
import { hasActiveIndexing } from './utils/indexing-progress';
import { isActiveConnectorSync } from './components/indexing-progress-indicator';
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
import { getIsAllRecordsMode, buildNavUrl as buildCleanNavUrl } from './utils/nav';
import { FOLDER_REINDEX_DEPTH, REINDEX_SELF_DEPTH, SIDEBAR_PAGINATION_PAGE_SIZE } from './constants';
import { UPLOAD_BATCH_CONFIG } from './constants/upload-batch.constants';
import { createSizeBatches } from './utils/batch-files';
import { sidebarNodeChildrenMetaFromResponse } from './utils/sidebar-child-pagination-meta';
import { refreshKbTree } from './utils/refresh-kb-tree';
import {
  getPrimaryReindexMenuLabelKey,
  getReindexLoadingTitle,
  getReindexNodeFromHubItem,
  getReindexSuccessTitle,
  needsReindexScopeModal,
  requiresForceReindexConfirmation,
  supportsBulkReindex,
  isKbCollectionNode,
} from './utils/reindex-label';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import type { ReindexMenuLabelKey } from './utils/reindex-label';
import { fetchAppDirectChildren } from './utils/fetch-app-direct-children';
import {
  resolveHubNodeNotFoundNavigation,
  resolvePostDeleteNavigation,
  shouldSilentlyRecoverHubNotFound,
} from './utils/post-delete-navigation';
import { toast } from '@/lib/store/toast-store';
import { FilePreviewSidebar, FilePreviewFullscreen } from '@/app/components/file-preview';
import {
  isPresentationFile,
  isDocxFile,
  isLegacyWordDocFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { useDebouncedSearch } from './hooks/use-debounced-search';
import { ErrorType, isProcessedError } from '@/lib/api/api-error';

function KnowledgeBasePageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();

  // View mode detection from query params
  const isAllRecordsMode = getIsAllRecordsMode(searchParams);
  const pageViewMode: PageViewMode = isAllRecordsMode ? 'all-records' : 'collections';

  const kbId = searchParams.get('kbId');
  const folderId = searchParams.get('folderId');
  const nodeId = searchParams.get('nodeId');

  /** Suppress global Not Found toast for fetches racing a post-delete URL update */
  const pendingSilentNotFoundNodeIdsRef = useRef(new Set<string>());

  const {
    // Collections mode state
    currentFolderId: storeFolderId,
    setCurrentFolderId,
    expandFolderExclusive,
    categorizedNodes,
    addNodes,
    setCategorizedNodes,
    cacheNodeChildren,
    clearNodeCacheEntries,
    purgeDeletedIdsFromSidebarChildrenCaches,
    tableData,
    isLoadingTableData,
    tableDataError,
    selectedNode,
    setTableData,
    setIsLoadingTableData,
    setTableDataError,
    setSelectedNode,
    patchVisibleRecordProgress,
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
    setAppRootListPagination,
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
  } = useKnowledgeBaseStore();

  // Extract filter and sort separately to create stable references
  const rawFilter = useKnowledgeBaseStore((state) => state.filter);
  const rawSort = useKnowledgeBaseStore((state) => state.sort);
  const isLoadingFlatCollections = useKnowledgeBaseStore((state) => state.isLoadingFlatCollections);
  const loadingAppIds = useKnowledgeBaseStore((state) => state.loadingAppIds);

  // Memoize filter and sort to prevent unnecessary re-renders and infinite loops
  // Only recreate when actual property values change, not on every render
  const filter = useMemo(() => rawFilter, [
    rawFilter.recordTypes?.join(','),
    rawFilter.indexingStatus?.join(','),
    rawFilter.origins?.join(','),
    rawFilter.connectorIds?.join(','),
    rawFilter.kbIds?.join(','),
    rawFilter.sizeRange,
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
    rawAllRecordsFilter.connectorIds?.join(','),
    rawAllRecordsFilter.sizeRange,
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
   * Single source of truth for the current Knowledge Base (collection) ID.
   *
   * Prefer matching the breadcrumb trail to KB roots in categorizedNodes (same
   * rule as sidebar auto-expand) so we are not tied to breadcrumbs[1] shape.
   * Fall back to breadcrumbs[1] then URL kbId for backward compatibility.
   */
  const selectedKbId = useMemo(() => {
    const currentTableData = isAllRecordsMode ? allRecordsTableData : tableData;
    const crumbs = currentTableData?.breadcrumbs;
    if (crumbs?.length && categorizedNodes) {
      const allRootNodes = [
        ...(categorizedNodes.shared ?? []),
        ...(categorizedNodes.private ?? []),
      ];
      const kbBreadcrumb = crumbs.find(
        (b) => allRootNodes.some((n) => n.id === b.id) || b.nodeType === 'kb'
      );
      if (kbBreadcrumb) return kbBreadcrumb.id;
    }
    return crumbs?.[1]?.id || kbId;
  }, [isAllRecordsMode, allRecordsTableData, tableData, kbId, categorizedNodes]);

  // Get table items directly from API response (no client-side filtering)
  const tableItems = useMemo(() => {
    return isAllRecordsMode
      ? allRecordsTableData?.items ?? []
      : tableData?.items ?? [];
  }, [isAllRecordsMode, allRecordsTableData?.items, tableData?.items]);

  // The container currently being browsed (its own aggregate is shown in the
  // header). Its rollup keeps polling alive during a reindex even when no child
  // row is individually active yet.
  const currentNodeRollup = useMemo(
    () =>
      (isAllRecordsMode ? allRecordsTableData : tableData)?.currentNode?.indexingRollup ?? null,
    [isAllRecordsMode, allRecordsTableData, tableData],
  );

  // Sync status of the connector currently being browsed (shown as a chip in the
  // header). Any active sync also keeps polling alive so the chip clears itself.
  const currentNodeSyncStatus = useMemo(
    () =>
      (isAllRecordsMode ? allRecordsTableData : tableData)?.currentNode?.syncStatus ?? null,
    [isAllRecordsMode, allRecordsTableData, tableData],
  );

  // Auto-poll for indexing status only while a visible record is still in
  // flight (QUEUED/IN_PROGRESS), or the container being viewed is still
  // aggregating. Gating on this boolean keeps the polling effect (and its
  // backoff) alive across refetches and stops the moment everything reaches a
  // terminal state, so settled views never poll.
  const hasActiveRecords = useMemo(
    () =>
      hasActiveIndexing(tableItems) ||
      currentNodeRollup?.isActive === true ||
      isActiveConnectorSync(currentNodeSyncStatus) ||
      tableItems.some((item) => isActiveConnectorSync((item as { syncStatus?: string }).syncStatus)),
    [tableItems, currentNodeRollup, currentNodeSyncStatus],
  );

  const collectionRootNodes = useMemo(
    () => [
      ...(categorizedNodes?.shared ?? []),
      ...(categorizedNodes?.private ?? []),
    ],
    [categorizedNodes]
  );
  const hasCollections = collectionRootNodes.length > 0;
  const kbApp = useMemo(() => appNodes.find((node) => isKbCollectionsHubApp(node)) ?? null, [appNodes]);

  /**
   * Initial collections load defers table fetch until the sidebar tree can run.
   * Must NOT flip on every categorizedNodes refresh (e.g. after delete) or we refetch
   * with stale searchParams while the URL still points at a removed node → 404 loops.
   */
  const collectionsNavigationReady = useMemo(() => {
    if (isAllRecordsMode) return true;
    const isKbAppLoading = kbApp ? loadingAppIds.has(kbApp.id) : false;
    return !(categorizedNodes === null && (isLoadingFlatCollections || isKbAppLoading));
  }, [isAllRecordsMode, kbApp, categorizedNodes, isLoadingFlatCollections, loadingAppIds]);

  // Search bar state
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  // Debounced search queries (300ms delay)
  const debouncedSearchQuery = useDebouncedSearch(searchQuery, 300);
  const debouncedAllRecordsSearchQuery = useDebouncedSearch(allRecordsSearchQuery, 300);

  // ========================================
  // URL ↔ Store Sync for Filters, Sort, Pagination, Search
  // ========================================

  const lastHydratedUrl = useRef('');

  // Read URL → Store: Always apply URL params to store when URL changes
  useEffect(() => {
    const currentUrl = searchParams.toString();

    // Skip if we already processed this exact URL (prevents echo from the write-effect below)
    if (currentUrl === lastHydratedUrl.current) return;

    // On initial mount, Next.js may have empty searchParams while location has the real query
    const locationSearch = typeof window !== 'undefined' ? window.location.search.replace(/^\?/, '') : '';
    if (!currentUrl && locationSearch.length > 0) return;

    lastHydratedUrl.current = currentUrl;

    const store = useKnowledgeBaseStore.getState();
    const allRecords = getIsAllRecordsMode(searchParams);

    if (allRecords) {
      const parsed = parseAllRecordsParams(searchParams);
      store.hydrateAllRecordsFilter(parsed.filter);
      store.setAllRecordsSort(parsed.sort);
      store.setAllRecordsLimit(parsed.limit);
      store.setAllRecordsSearchQuery(parsed.searchQuery);
      store.setAllRecordsPage(parsed.page);
      setIsSearchOpen(!!parsed.searchQuery);
    } else {
      const parsed = parseCollectionsParams(searchParams);
      store.hydrateFilter(parsed.filter);
      store.setSort(parsed.sort);
      store.setCollectionsLimit(parsed.limit);
      store.setSearchQuery(parsed.searchQuery);
      store.setCollectionsPage(parsed.page);
      setIsSearchOpen(!!parsed.searchQuery);
    }
  }, [searchParams]);

  // Write Store → URL: Sync filter/sort/pagination changes to URL
  useEffect(() => {
    const store = useKnowledgeBaseStore.getState();
    const allRecords = getIsAllRecordsMode(searchParams);

    // Build URL from current store state
    const baseParams: Record<string, string> = {};
    if (allRecords) baseParams.view = 'all-records';
    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');
    if (nodeType) baseParams.nodeType = nodeType;
    if (nodeId) baseParams.nodeId = nodeId;

    const filterParams = allRecords
      ? serializeAllRecordsParams(
          store.allRecordsFilter,
          store.allRecordsSort,
          { page: store.allRecordsPagination.page, limit: store.allRecordsPagination.limit },
          debouncedAllRecordsSearchQuery
        )
      : serializeCollectionsParams(
          store.filter,
          store.sort,
          { page: store.collectionsPagination.page, limit: store.collectionsPagination.limit },
          debouncedSearchQuery
        );

    const newUrl = buildFilterUrl(baseParams, filterParams);
    const currentUrl = `/knowledge-base${searchParams.toString() ? `?${searchParams.toString()}` : ''}`;

    // Only replace if URL actually changed (prevents infinite loops)
    if (newUrl !== currentUrl) {
      // Update the hydration ref so the read-effect skips this URL (it came from us, not navigation)
      lastHydratedUrl.current = new URL(newUrl, 'http://x').searchParams.toString();
      router.replace(newUrl);
    }
  }, [
    filter, sort, collectionsPagination.page, collectionsPagination.limit, debouncedSearchQuery,
    allRecordsFilter, allRecordsSort, allRecordsPagination.page, allRecordsPagination.limit, debouncedAllRecordsSearchQuery,
    isAllRecordsMode,
    searchParams,
    router,
  ]);

  // Check if any filters are active (for empty state messaging)
  const hasActiveFilters = useMemo(() => {
    if (isAllRecordsMode) {
      return !!(
        allRecordsFilter.recordTypes?.length ||
        allRecordsFilter.indexingStatus?.length ||
        allRecordsFilter.sizeRange ||
        allRecordsFilter.createdAfter ||
        allRecordsFilter.createdBefore ||
        allRecordsFilter.updatedAfter ||
        allRecordsFilter.updatedBefore ||
        allRecordsFilter.origins?.length ||
        allRecordsFilter.connectorIds?.length
      );
    }
    return !!(
      filter.recordTypes?.length ||
      filter.indexingStatus?.length ||
      filter.sizeRange ||
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
  const [moveDialogLoadingIds, setMoveDialogLoadingIds] = useState<Set<string>>(new Set());

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

  // Reindex scope dialog (indexable parent records with children)
  const [reindexScopePending, setReindexScopePending] = useState<{
    item: KnowledgeHubNode | AllRecordItem;
    primaryLabelKey: ReindexMenuLabelKey;
  } | null>(null);
  const [forceReindexPending, setForceReindexPending] = useState<{
    item: KnowledgeHubNode | AllRecordItem;
    statusFilters?: string[];
  } | null>(null);
  const [isReindexSubmitting, setIsReindexSubmitting] = useState(false);

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
    webUrl?: string;
    previewRenderable?: boolean;
  } | null>(null);
  const [previewMode, setPreviewMode] = useState<'sidebar' | 'fullscreen'>('sidebar');

  // Upload store actions
  const { addItems: addUploadItems, startUpload, completeUpload, failUpload } = useUploadStore();
  const { maxFileSizeBytes, maxFileSizeMB } = useUploadLimits();

  /**
   * Tracks last page we fetched for so filter/sort resets (page → 1) do not trigger a
   * duplicate fetch from the pagination effect when page actually changes (e.g. 2 → 1).
   */
  const prevAllRecordsPageRef = useRef(allRecordsPagination.page);

  // Sync current folder from URL params (Collections mode only).
  // In All Records mode the active node is tracked via nodeId + auto-expansion
  // in the sidebar slot. Skipping here prevents setCurrentFolderId(null) from
  // racing with and overwriting the sidebar slot's setCurrentFolderId(nodeId).
  useEffect(() => {
    if (isAllRecordsMode) return;
    if (searchParams.get('nodeId')) return;
    setCurrentFolderId(folderId);
  }, [folderId, isAllRecordsMode, setCurrentFolderId, searchParams]);

  useEffect(() => {
    async function fetchAppNodesAllRecords() {
      try {
        setLoadingFlatCollections(true);
        const response = await KnowledgeHubApi.getNavigationNodes({
          page: 1,
          limit: SIDEBAR_PAGINATION_PAGE_SIZE,
          include: 'counts',
          sortBy: 'updatedAt',
          sortOrder: 'desc',
        });

        // Filter to app-type nodes only (root can return other node types)
        const appItems = response.items.filter((n) => n.nodeType === 'app');
        const kbApps = appItems.filter((n) => isKbCollectionsHubApp(n));
        const connectorApps = appItems.filter((n) => !isKbCollectionsHubApp(n));
        setAppNodes([...kbApps, ...connectorApps]);

        const p = response.pagination;
        setAppRootListPagination(
          p
            ? {
                hasNext: p.hasNext,
                nextPage: p.hasNext ? p.page + 1 : p.page,
              }
            : null
        );
      } catch (error) {
        console.error('Error fetching app nodes:', error);
        toast.error('Failed to load sidebar', {
          description: 'Could not load app sections. Please refresh the page.',
        });
      } finally {
        setLoadingFlatCollections(false);
      }
    }

    async function fetchAppNodesCollections() {
      try {
        setLoadingFlatCollections(true);
        const response = await KnowledgeHubApi.getNavigationNodes({
          page: 1,
          limit: SIDEBAR_PAGINATION_PAGE_SIZE,
          include: 'counts',
          sortBy: 'updatedAt',
          sortOrder: 'desc',
        });
        const appItems = response.items.filter((n) => n.nodeType === 'app');
        const kbApps = appItems.filter((n) => isKbCollectionsHubApp(n));
        const connectorApps = appItems.filter((n) => !isKbCollectionsHubApp(n));
        setAppNodes([...kbApps, ...connectorApps]);

        const p = response.pagination;
        setAppRootListPagination(
          p
            ? {
                hasNext: p.hasNext,
                nextPage: p.hasNext ? p.page + 1 : p.page,
              }
            : null
        );
      } catch (error) {
        console.error('Error fetching KB app nodes:', error);
        toast.error('Failed to load Collections', {
          description: 'Could not load collections. Please refresh the page.',
        });
        setAppNodes([]);
        setAppRootListPagination(null);
      } finally {
        setLoadingFlatCollections(false);
      }
    }

    if (isAllRecordsMode) {
      void fetchAppNodesAllRecords();
    } else {
      void fetchAppNodesCollections();
    }
  }, [
    isAllRecordsMode,
    setAppNodes,
    setAppRootListPagination,
    setLoadingFlatCollections,
  ]);

  // Collections mode: use KB apps themselves as the collections in the sidebar tree.
  // Each KB is now a standalone root-level app (no hub/children model).
  useEffect(() => {
    if (appNodes.length === 0 || isAllRecordsMode) return;

    const { setNodes, setCategorizedNodes } = useKnowledgeBaseStore.getState();
    const kbApps = appNodes.filter((n) => isKbCollectionsHubApp(n));
    if (kbApps.length > 0) {
      setNodes(kbApps);
      setCategorizedNodes(categorizeNodes(kbApps, null));
    }
  }, [appNodes, isAllRecordsMode]);

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
    // setAllRecordsFilter / setAllRecordsSort reset page to 1; sync ref so pagination effect
    // does not duplicate-fetch when page changes (e.g. 2 → 1), without a skip flag that can
    // stick when page stays 1 and the pagination effect never runs.
    prevAllRecordsPageRef.current =
      useKnowledgeBaseStore.getState().allRecordsPagination.page;
    fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
  }, [allRecordsFilter, allRecordsSort]);

  // All Records mode: Re-fetch when pagination page changes
  useEffect(() => {
    if (!isAllRecordsMode) return;
    if (allRecordsPagination.page === prevAllRecordsPageRef.current) return;
    prevAllRecordsPageRef.current = allRecordsPagination.page;
    fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
  }, [allRecordsPagination.page]);

  // All Records mode: Re-fetch when pagination limit changes
  useEffect(() => {
    if (!isAllRecordsMode || allRecordsPagination.limit === DEFAULT_PAGE_SIZE) return;
    // setAllRecordsLimit resets page to 1; keep pagination ref in sync (same as filter effect).
    prevAllRecordsPageRef.current =
      useKnowledgeBaseStore.getState().allRecordsPagination.page;
    fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
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

        const urlNodeIdForFetch =
          searchParams.get('nodeId') ?? searchParams.get('folderId');
        const tableSnapshot = useKnowledgeBaseStore.getState().tableData;
        const suppressNotFoundToast = shouldSilentlyRecoverHubNotFound({
          failedNodeId: nodeId,
          urlNodeId: urlNodeIdForFetch,
          tableData: tableSnapshot,
          pendingSilentNotFoundNodeIds: pendingSilentNotFoundNodeIdsRef.current,
        });

        const data = await KnowledgeHubApi.loadFolderData(
          nodeType as NodeType,
          nodeId,
          params,
          { suppressErrorToast: suppressNotFoundToast }
        );

        pendingSilentNotFoundNodeIdsRef.current.delete(nodeId);

        setTableData(data);

        // Update pagination from response
        if (data.pagination) {
          setCollectionsPagination(data.pagination);
        }
        setSelectedNode({ nodeType, nodeId });

        if (data.breadcrumbs && data.breadcrumbs.length > 0) {
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
                  onlyContainers: true,
                  page: 1,
                  limit: 50,
                });
                const kbEffectiveHasChildFolders = effectiveHasChildrenAfterSidebarExpand(
                  kbChildren.items,
                );

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
                      kbChildren.items,
                      kbEffectiveHasChildFolders
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
              expandFolderExclusive(breadcrumb.id);

              const iterState = useKnowledgeBaseStore.getState();

              if (!iterState.nodeChildrenCache.has(breadcrumb.id)) {
                try {
                  const folderChildren = await KnowledgeHubApi.getNodeChildren(
                    breadcrumb.nodeType as NodeType,
                    breadcrumb.id,
                    { onlyContainers: true, page: 1, limit: 50 }
                  );
                  const effectiveHasChildFolders = effectiveHasChildrenAfterSidebarExpand(
                    folderChildren.items,
                  );

                  cacheNodeChildren(breadcrumb.id, folderChildren.items);
                  addNodes(folderChildren.items);

                  const mergeState = useKnowledgeBaseStore.getState();
                  if (mergeState.categorizedNodes) {
                    const parentNode = mergeState.nodes.find((n) => n.id === breadcrumb.id);
                    if (parentNode) {
                      const section = categorizeNode(parentNode);
                      const updatedTree = mergeChildrenIntoTree(
                        mergeState.categorizedNodes[section],
                        breadcrumb.id,
                        folderChildren.items,
                        effectiveHasChildFolders
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

        useKnowledgeBaseStore.getState().reMergeCachedChildrenIntoTree();

      } catch (error) {
        const status = isProcessedError(error) ? error.statusCode : (error as { statusCode?: number })?.statusCode;
        const isNotFound =
          status === 404 || (isProcessedError(error) && error.type === ErrorType.NOT_FOUND);
        if (status === 403) {
          await handleAccessRevoked();
        } else if (isNotFound) {
          useKnowledgeBaseStore.getState().purgeDeletedIdsFromSidebarChildrenCaches([nodeId]);
          const td = useKnowledgeBaseStore.getState().tableData;
          const urlNodeId = searchParams.get('nodeId') ?? searchParams.get('folderId');
          pendingSilentNotFoundNodeIdsRef.current.delete(nodeId);

          const nav = resolveHubNodeNotFoundNavigation(nodeId, {
            breadcrumbs: td?.breadcrumbs ?? null,
            urlNodeId,
            currentNodeId: td?.currentNode?.id ?? null,
            parentNode: td?.parentNode ?? null,
          });
          if (nav.kind === 'navigate') {
            clearTableData();
            router.replace(
              buildNavUrlFn(
                { nodeType: nav.nodeType, nodeId: nav.nodeId },
                isAllRecordsMode,
                debouncedSearchQuery,
                debouncedAllRecordsSearchQuery
              )
            );
            await refreshKbTree();
          } else {
            // nav.kind === 'root' (or unreachable 'none' — resolveHubNodeNotFoundNavigation
            // guarantees urlNodeId is at least failedNodeId so urlOrCurrentHit is always true).
            clearTableData();
            router.replace(
              buildNavUrlFn({}, isAllRecordsMode, debouncedSearchQuery, debouncedAllRecordsSearchQuery)
            );
            await refreshKbTree();
          }
        } else {
          console.error('Failed to fetch table data:', error);
          setTableDataError('Failed to load items. Please try again.');
          setTableData(null);
        }
      } finally {
        setIsLoadingTableData(false);
      }
    },
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
      clearTableData,
      refreshKbTree,
      router,
      searchParams,
      isAllRecordsMode,
      debouncedSearchQuery,
      debouncedAllRecordsSearchQuery,
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
  // Fetch all of the user's collections (KB apps) as a paginated list when no
  // specific collection is selected. Without file-level filters, force browse mode
  // (flattened=false) so the API returns app-level collection nodes; with
  // filters, omit flattened and let the backend use search mode.
  const fetchAllCollectionsData = useCallback(async () => {
    setIsLoadingTableData(true);
    setTableDataError(null);
    try {
      const currentState = useKnowledgeBaseStore.getState();
      const currentPagination = currentState.collectionsPagination;
      const currentFilter = currentState.filter;
      const currentSearchQuery = currentState.searchQuery;
      const currentSort = currentState.sort;

      const params = buildFilterParams(
        { ...currentFilter, searchQuery: currentSearchQuery },
        currentSort,
        currentPagination
      );

      const useFlattenedSearch = hasKnowledgeHubFlatteningFilters(
        currentFilter,
        currentSearchQuery
      );

      const data = await KnowledgeHubApi.getNavigationNodes(
        useFlattenedSearch
          ? {
              // File-level filters: search records across collections (not app nodes).
              ...params,
              origins: 'COLLECTION',
            }
          : {
              // Default list: browse collection app nodes.
              ...params,
              nodeTypes: 'app',
              origins: 'COLLECTION',
              flattened: false,
            }
      );

      setTableData(data);
      setSelectedNode(null);
      if (data.pagination) {
        setCollectionsPagination(data.pagination);
      }
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      setTableDataError(err?.response?.data?.message || err?.message || 'Failed to load collections');
    } finally {
      setIsLoadingTableData(false);
    }
  }, [setIsLoadingTableData, setTableDataError, setTableData, setSelectedNode, setCollectionsPagination]);

  useEffect(() => {
    if (isAllRecordsMode) return;
    if (!collectionsNavigationReady) return;

    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');

    if (nodeType && nodeId) {
      fetchTableData(nodeType, nodeId);
    } else if (hasCollections) {
      // No collection selected — show all collections as a list instead of
      // auto-navigating into the first one.
      void fetchAllCollectionsData();
    } else {
      clearTableData();
    }
  }, [
    isAllRecordsMode,
    collectionsNavigationReady,
    searchParams,
    fetchTableData,
    fetchAllCollectionsData,
    clearTableData,
    hasCollections,
  ]);

  // Track if search has been used (to distinguish initial mount from cleared search)
  const hasSearchedCollections = useRef(false);
  const hasSearchedAllRecords = useRef(false);

  // Collections mode: Fetch table data when debounced search query changes
  // This effect runs when the user types in the search bar and the debounced value updates
  useEffect(() => {
    // Skip if in All Records mode or no node selected
    if (isAllRecordsMode || !selectedNode) return;

    // Skip on initial mount when search is empty and hasn't been used yet
    if (!debouncedSearchQuery && !hasSearchedCollections.current) return;

    // Mark that search has been used
    if (debouncedSearchQuery) {
      hasSearchedCollections.current = true;
    }

    // Only fetch if we have a selected node to search within
    fetchTableData(selectedNode.nodeType, selectedNode.nodeId);
  }, [debouncedSearchQuery, isAllRecordsMode, selectedNode?.nodeType, selectedNode?.nodeId]);

  // All Records mode: Fetch table data when debounced search query changes
  // This effect runs when the user types in the search bar and the debounced value updates
  useEffect(() => {
    // Skip if not in All Records mode
    if (!isAllRecordsMode) return;

    // Skip on initial mount when search is empty and hasn't been used yet
    if (!debouncedAllRecordsSearchQuery && !hasSearchedAllRecords.current) return;

    // Mark that search has been used
    if (debouncedAllRecordsSearchQuery) {
      hasSearchedAllRecords.current = true;
    }

    // setAllRecordsSearchQuery resets page to 1; keep pagination ref in sync (same as filter effect).
    prevAllRecordsPageRef.current =
      useKnowledgeBaseStore.getState().allRecordsPagination.page;
    fetchAllRecordsTableData(allRecordsNodeType ?? undefined, allRecordsNodeId ?? undefined);
  }, [debouncedAllRecordsSearchQuery, isAllRecordsMode]);

  // Sync page view mode to Zustand store (for loading states and other store consumers)
  useEffect(() => {
    setCurrentViewMode(pageViewMode);
  }, [pageViewMode, setCurrentViewMode]);

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
        hasChildren: node.hasChildren,
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
    urlParams.set('nodeType', 'app');
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
      prevAllRecordsPageRef.current =
        useKnowledgeBaseStore.getState().allRecordsPagination.page;
      // Immediate refetch when clearing search (bypasses debounce).
      // Preserve drill-down: fetchAllRecordsTableData() with no args loads the global root only.
      const nt = searchParams.get('nodeType');
      const nid = searchParams.get('nodeId');
      if (nt && nid) {
        void fetchAllRecordsTableData(nt, nid);
      } else {
        void fetchAllRecordsTableData();
      }
    } else {
      setSearchQuery('');
      // Immediate refetch when clearing search (bypasses debounce)
      if (selectedNode) {
        void fetchTableData(selectedNode.nodeType, selectedNode.nodeId);
      }
    }
  }, [
    isAllRecordsMode,
    searchParams,
    selectedNode,
    setAllRecordsSearchQuery,
    setSearchQuery,
    fetchAllRecordsTableData,
    fetchTableData,
  ]);

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

  const refreshAllRecordsSidebarForCurrentRoute = useCallback(async () => {
    if (!isAllRecordsMode) return;

    const nodeType = searchParams.get('nodeType') as NodeType | null;
    const nodeId = searchParams.get('nodeId');
    if (!nodeType || !nodeId) return;

    try {
      const response = await KnowledgeHubApi.getNodeChildren(nodeType, nodeId, {
        onlyContainers: true,
        page: 1,
        limit: SIDEBAR_PAGINATION_PAGE_SIZE,
        include: 'counts',
        sortBy: 'name',
        sortOrder: 'asc',
      });

      const effectiveHasChildFolders = effectiveHasChildrenAfterSidebarExpand(response.items);
      const state = useKnowledgeBaseStore.getState();
      const selectedApp = nodeType === 'app' ? state.appNodes.find((app) => app.id === nodeId) : null;

      if (selectedApp) {
        state.cacheAppChildren(selectedApp.id, response.items);
        const p = response.pagination;
        state.setAppChildPagination(
          selectedApp.id,
          p
            ? {
                hasNext: p.hasNext,
                nextPage: p.hasNext ? p.page + 1 : p.page,
              }
            : { hasNext: false, nextPage: 1 }
        );

        if (!isKbCollectionsHubApp(selectedApp)) {
          state.addNodes(response.items);
          state.setConnectorAppTree(
            selectedApp.id,
            buildConnectorAppSidebarTree(selectedApp.id, response.items)
          );
        }
        return;
      }

      state.cacheNodeChildren(nodeId, response.items);
      state.setNodeChildrenPagination(
        nodeId,
        sidebarNodeChildrenMetaFromResponse(
          response.pagination,
          response.items.length,
          SIDEBAR_PAGINATION_PAGE_SIZE,
          nodeType
        )
      );
      state.addNodes(response.items);

      const latest = useKnowledgeBaseStore.getState();
      if (latest.categorizedNodes) {
        const parentNode = latest.nodes.find((n) => n.id === nodeId);
        if (parentNode) {
          const section = categorizeNode(parentNode);
          const updatedTree = mergeChildrenIntoTree(
            latest.categorizedNodes[section],
            nodeId,
            response.items,
            effectiveHasChildFolders
          );
          latest.setCategorizedNodes({ ...latest.categorizedNodes, [section]: updatedTree });
        }
      }

      for (const [appId, tree] of Array.from(latest.connectorAppTrees.entries())) {
        if (!treeHasNodeWithId(tree, nodeId)) continue;
        latest.mergeConnectorAppTreeChildren(appId, nodeId, response.items, effectiveHasChildFolders);
        break;
      }
    } catch (error) {
      console.error('Failed to refresh all-records sidebar for current route', { nodeId, error });
    }
  }, [isAllRecordsMode, searchParams]);

  const refetchMainTableForCurrentRoute = useCallback(async () => {
    const nodeType = searchParams.get('nodeType');
    const nodeId = searchParams.get('nodeId');

    if (isAllRecordsMode) {
      if (nodeType && nodeId) {
        await fetchAllRecordsTableData(nodeType, nodeId);
      } else {
        await fetchAllRecordsTableData();
      }
    } else if (nodeType && nodeId) {
      await fetchTableData(nodeType, nodeId);
    } else if (hasCollections) {
      await fetchAllCollectionsData();
    } else {
      clearTableData();
    }
  }, [
    isAllRecordsMode,
    searchParams,
    fetchTableData,
    fetchAllRecordsTableData,
    fetchAllCollectionsData,
    hasCollections,
    clearTableData,
  ]);

  const pollVisibleRecordProgress = useCallback(async () => {
    const currentState = useKnowledgeBaseStore.getState();
    if (isAllRecordsMode) {
      const params = buildAllRecordsFilterParams(
        { ...currentState.allRecordsFilter, searchQuery: currentState.allRecordsSearchQuery },
        currentState.allRecordsSort,
        currentState.allRecordsPagination
      );
      // Polling only patches row/current-node progress. Do not recompute counts,
      // breadcrumbs or filter facets on every refresh.
      params.include = 'indexingRollup';
      const nodeType = searchParams.get('nodeType');
      const nodeId = searchParams.get('nodeId');
      const data = nodeType && nodeId
        ? await KnowledgeHubApi.loadFolderData(nodeType as NodeType, nodeId, params, {
            suppressErrorToast: true,
          })
        : await KnowledgeHubApi.getAllRootItems(params, { suppressErrorToast: true });
      patchVisibleRecordProgress(data.items, data.currentNode);
      return;
    }

    const nodeType = selectedNode?.nodeType ?? searchParams.get('nodeType');
    const nodeId = selectedNode?.nodeId ?? searchParams.get('nodeId') ?? searchParams.get('folderId');

    const params = buildFilterParams(
      { ...currentState.filter, searchQuery: currentState.searchQuery },
      currentState.sort,
      currentState.collectionsPagination
    );
    params.include = 'indexingRollup';
    if (!nodeType || !nodeId) {
      const useFlattenedSearch = hasKnowledgeHubFlatteningFilters(
        currentState.filter,
        currentState.searchQuery
      );
      const data = await KnowledgeHubApi.getNavigationNodes(
        useFlattenedSearch
          ? { ...params, origins: 'COLLECTION' }
          : { ...params, nodeTypes: 'app', origins: 'COLLECTION', flattened: false },
        { suppressErrorToast: true }
      );
      patchVisibleRecordProgress(data.items, data.currentNode);
      return;
    }
    const data = await KnowledgeHubApi.loadFolderData(nodeType as NodeType, nodeId, params, {
      suppressErrorToast: true,
    });
    patchVisibleRecordProgress(data.items, data.currentNode);
  }, [isAllRecordsMode, patchVisibleRecordProgress, searchParams, selectedNode]);

  const pollProgressRef = useRef(pollVisibleRecordProgress);
  pollProgressRef.current = pollVisibleRecordProgress;

  useEffect(() => {
    if (!hasActiveRecords) return undefined;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();

    const nextDelay = (): number => {
      const elapsed = Date.now() - startedAt;
      if (elapsed < 60_000) return 4_000;
      if (elapsed < 5 * 60_000) return 10_000;
      return 30_000;
    };

    const schedule = () => {
      timer = setTimeout(async () => {
        if (cancelled) return;
        // Skip the network round-trip while the tab is hidden, but keep the
        // loop alive so it resumes promptly when the user returns.
        if (typeof document === 'undefined' || !document.hidden) {
          try {
            await pollProgressRef.current();
          } catch {
            // Best-effort; a transient failure shouldn't kill the poll loop.
          }
        }
        if (!cancelled) schedule();
      }, nextDelay());
    };

    if (typeof document === 'undefined' || !document.hidden) {
      void pollProgressRef.current().catch(() => {
        // Best-effort; a transient failure shouldn't kill the poll loop.
      });
    }
    schedule();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [hasActiveRecords]);

  // Handle refresh
  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      await refetchMainTableForCurrentRoute();
      await refreshAllRecordsSidebarForCurrentRoute();
    } finally {
      setIsRefreshing(false);
    }
  }, [refetchMainTableForCurrentRoute, refreshAllRecordsSidebarForCurrentRoute, setIsRefreshing]);

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

  /** After deletes (or store-driven refresh with deleted ids), navigate to parent/root or full refresh. */
  const refreshDataAfterDelete = useCallback(
    async (deletedIds?: string[]) => {
      if (isAllRecordsMode || !deletedIds?.length) {
        await refreshData();
        return;
      }

      for (const id of deletedIds) {
        if (id) pendingSilentNotFoundNodeIdsRef.current.add(id);
      }

      // Purge deleted nodes from the sidebar cache. Runs synchronously before the first
      // await, giving instant visual feedback. When called from store.deleteNode the store
      // has already done an optimistic purge; this second call is idempotent (no-op).
      // Needed here for direct-API callers (e.g. handleSidebarDeleteConfirm) that do NOT
      // go through store.deleteNode.
      purgeDeletedIdsFromSidebarChildrenCaches(deletedIds);

      const snapshot = useKnowledgeBaseStore.getState().tableData;
      const urlNodeId = searchParams.get('nodeId') ?? searchParams.get('folderId');
      const nav = resolvePostDeleteNavigation({
        deletedIds,
        breadcrumbs: snapshot?.breadcrumbs ?? null,
        urlNodeId,
        currentNodeId: snapshot?.currentNode?.id ?? null,
        parentNode: snapshot?.parentNode ?? null,
      });

      if (nav.kind === 'navigate') {
        clearTableData();
        // Navigate before sidebar refresh: refreshKbTree updates categorizedNodes and retriggers
        // the URL→fetch effect while searchParams can still point at the deleted node → 404 loop.
        router.replace(buildNavUrl({ nodeType: nav.nodeType, nodeId: nav.nodeId }));
        await refreshKbTree();
        return;
      }
      if (nav.kind === 'root') {
        clearTableData();
        router.replace(buildNavUrl({}));
        await refreshKbTree();
        return;
      }
      await refreshData();
    },
    [
      isAllRecordsMode,
      refreshData,
      searchParams,
      clearTableData,
      refreshKbTree,
      router,
      buildNavUrl,
      purgeDeletedIdsFromSidebarChildrenCaches,
    ]
  );

  // Handle create folder - context-aware
  const handleCreateFolder = useCallback(() => {
    const currentNode = tableData?.currentNode;

    if (currentNode?.nodeType === 'app') {
      // At KB app root level — create a root-level folder inside this KB
      setCreateFolderContext({
        type: 'subfolder' as const,
        kbId: currentNode.id,
        parentId: null,
        parentName: currentNode.name,
      });
      setIsCreateFolderDialogOpen(true);
    } else if (currentNode?.nodeType === 'folder') {
      // Inside a folder — find the KB root from breadcrumbs (app or kb nodeType)
      const kbBreadcrumb = tableData?.breadcrumbs?.find(
        (b) => b.nodeType === 'app' || b.nodeType === 'kb'
      );
      const kbId = kbBreadcrumb?.id ?? tableData?.breadcrumbs?.[0]?.id;
      if (kbId) {
        setCreateFolderContext({
          type: 'subfolder' as const,
          kbId,
          parentId: currentNode.id,
          parentName: currentNode.name,
        });
        setIsCreateFolderDialogOpen(true);
      } else {
        // Fallback: no KB root found — create a new collection
        setCreateFolderContext({ type: 'collection' });
        setIsCreateFolderDialogOpen(true);
      }
    } else {
      // No KB context (at root or no currentNode) — create new collection
      setCreateFolderContext({ type: 'collection' });
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
          router.push(buildNavUrl({ nodeType: 'app', nodeId: newCollection.id }));

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

      // Client-side size gate: reject files exceeding the platform limit before
      // any network request, giving the user instant feedback and saving bandwidth.
      const oversized: FileEntry[] = [];
      const validEntries: FileEntry[] = [];
      for (const entry of fileEntries) {
        if (entry.file.size > maxFileSizeBytes) {
          oversized.push(entry);
        } else {
          validEntries.push(entry);
        }
      }

      // One upload session per drag-drop, shared by all of this drop's batch
      // requests. The SSE stream keyed by `uploadId` delivers async per-file
      // failures and lets the tracker survive a refresh / connection drop.
      const uploadId =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : generateUploadId();
      const sessionLabel =
        items.length === 1 && items[0].type === 'folder'
          ? items[0].name
          : `${fileEntries.length} file${fileEntries.length === 1 ? '' : 's'}`;
      useUploadStore.getState().upsertSession({
        uploadId,
        kbId: selectedKbId,
        folderId: newParentId,
        label: sessionLabel,
      });

      // Add ALL file entries (including oversized) to the upload store so they
      // appear in the tracker — oversized ones are failed immediately below.
      addUploadItems(
        fileEntries.map((entry) => ({
          id: entry.storeId,
          name: entry.file.name,
          type: 'file' as const,
          size: entry.file.size,
          file: entry.file,
          knowledgeBaseId: selectedKbId,
          parentId: newParentId,
          uploadId,
          filePath: entry.filePath,
        }))
      );

      setIsUploadSidebarOpen(false);
      setIsUploading(false);

      // Immediately fail oversized files with the same error the backend would send
      for (const entry of oversized) {
        failUpload(
          entry.storeId,
          `File exceeds the ${maxFileSizeMB} MB size limit`,
          [FileRejectionReason.EXCEEDS_SIZE_LIMIT],
        );
      }

      // If every file was oversized, finalize the session and show failures
      if (validEntries.length === 0) {
        useUploadStore.getState().finalizeSession(uploadId);
        const sessionStoreIds = new Set(fileEntries.map((e) => e.storeId));
        const failedInSession = useUploadStore
          .getState()
          .items.filter((i) => sessionStoreIds.has(i.id) && i.status === 'failed');
        notifyUploadFailures(failedInSession, maxFileSizeMB);
        return;
      }

      // Mark only valid files as uploading — oversized ones are already failed
      validEntries.forEach((entry) => startUpload(entry.storeId));

      let anySuccess = false;

      const normalizePath = (p: string) =>
        p.replace(/\\/g, '/').replace(/^\/+/, '').trim();

      // Stream one batch upload: the POST response is an SSE stream of per-file
      // outcomes (rejections, storage upload, indexing). Map each event to its
      // row by file path within the batch.
      const sendBatch = async (batch: FileEntry[]) => {
        const batchFiles = batch.map((e) => e.file);
        const batchMetadata: FileMetadata[] = batch.map((e) => ({
          file_path: e.filePath,
          last_modified: e.file.lastModified,
        }));
        const byPath = new Map(
          batch.map((e) => [normalizePath(e.filePath), e.storeId]),
        );
        const findStoreId = (data: any): string | undefined =>
          byPath.get(
            normalizePath(String(data?.filePath || data?.fileName || '')),
          );

        let streamError: Error | null = null;
        // The server emits a terminal `done` once the whole batch is processed,
        // or an `error` event on a catastrophic mid-stream failure. We only
        // treat unresolved rows as successful when `done` was actually seen.
        let gotDone = false;
        let gotError: string | null = null;
        await KnowledgeBaseApi.streamUpload(
          selectedKbId,
          newParentId,
          batchFiles,
          batchMetadata,
          {
            onEvent: (evt) => {
              const data = evt.data as any;
              if (evt.event === 'file:succeeded') {
                const id = findStoreId(data);
                if (id) completeUpload(id);
                anySuccess = true;
              } else if (evt.event === 'file:failed') {
                const id = findStoreId(data);
                if (id) {
                  const errors =
                    Array.isArray(data?.errors) && data.errors.length
                      ? data.errors
                      : [
                          t('uploadProgress.uploadFailedDefault', {
                            defaultValue: 'Upload failed',
                          }),
                        ];
                  const reason = parseFileRejectionReason(data?.reason);
                  failUpload(id, errors, reason ? [reason] : undefined);
                }
              } else if (evt.event === 'done') {
                gotDone = true;
              } else if (evt.event === 'error') {
                gotError =
                  (data && typeof data.message === 'string' && data.message) ||
                  t('uploadProgress.serverError', {
                    defaultValue: 'Upload failed on the server',
                  });
              }
            },
            onError: (err) => {
              streamError = err;
            },
          },
        );

        // Resolve any row whose terminal event was missed. NEVER mark a row
        // completed unless the server confirmed the batch finished (`done`) and
        // no error occurred — otherwise fail it, so a dropped/errored stream is
        // never shown as success.
        const message =
          (streamError as Error | null)?.message ||
          gotError ||
          t('uploadProgress.uploadIncomplete', {
            defaultValue: 'Upload incomplete',
          });
        const succeededCleanly = gotDone && !gotError && !streamError;
        const items = useUploadStore.getState().items;
        batch.forEach((e) => {
          const item = items.find((i) => i.id === e.storeId);
          if (item && (item.status === 'uploading' || item.status === 'pending')) {
            if (succeededCleanly) {
              completeUpload(e.storeId);
              // A row whose terminal event we missed but whose batch finished
              // cleanly was created server-side — warrant a refresh.
              anySuccess = true;
            } else {
              failUpload(e.storeId, message);
            }
          }
        });

        if (streamError) {
          // Connection / transport failure — surface it so the worker/priming
          // handlers can also account for the batch.
          throw streamError;
        }
        // NOTE: `anySuccess` is set only when at least one file actually
        // succeeded (file:succeeded event, or a cleanly-finished missed row), so
        // an all-failed batch does not trigger a pointless table refresh.
      };

      // Group entries by top-level folder so each distinct folder gets a
      // serial "priming" batch that creates the folder node on the backend
      // before the remaining batches for that folder run concurrently.
      const getTopLevelFolder = (filePath: string): string | null => {
        const idx = filePath.indexOf('/');
        return idx !== -1 ? filePath.substring(0, idx) : null;
      };

      const folderGroups = new Map<string, FileEntry[]>();
      const standaloneEntries: FileEntry[] = [];

      for (const entry of validEntries) {
        const folder = getTopLevelFolder(entry.filePath);
        if (folder) {
          if (!folderGroups.has(folder)) folderGroups.set(folder, []);
          folderGroups.get(folder)!.push(entry);
        } else {
          standaloneEntries.push(entry);
        }
      }

      const primingBatches: FileEntry[][] = [];
      const remainingBatches: FileEntry[][] = [];

      for (const [, entries] of folderGroups) {
        const folderBatches = createSizeBatches(entries);
        folderBatches.forEach((batch, i) => {
          if (i === 0) {
            primingBatches.push(batch);
          } else {
            remainingBatches.push(batch);
          }
        });
      }

      remainingBatches.push(...createSizeBatches(standaloneEntries));

      // Send priming batches sequentially to establish folder nodes
      const failedFolders = new Set<string>();
      for (const batch of primingBatches) {
        try {
          await sendBatch(batch);
        } catch (error) {
          const errorMessage =
            error instanceof Error
              ? error.message
              : t('uploadProgress.uploadFailedDefault', {
                  defaultValue: 'Upload failed',
                });
          batch.forEach((entry) => failUpload(entry.storeId, errorMessage));
          const folder = getTopLevelFolder(batch[0].filePath);
          if (folder) failedFolders.add(folder);
        }
      }

      // Fail remaining batches whose folder priming batch failed
      const viableBatches = remainingBatches.filter((batch) => {
        const folder = getTopLevelFolder(batch[0]?.filePath);
        if (folder && failedFolders.has(folder)) {
          batch.forEach((entry) =>
            failUpload(
              entry.storeId,
              t('uploadProgress.skippedFolderFailed', {
                defaultValue: 'Skipped: folder creation failed',
              })
            )
          );
          return false;
        }
        return true;
      });

      // Run viable batches concurrently via worker pool
      let nextPoolIdx = 0;
      const poolWorker = async () => {
        while (true) {
          const idx = nextPoolIdx++;
          if (idx >= viableBatches.length) break;
          try {
            await sendBatch(viableBatches[idx]);
          } catch (error) {
            const errorMessage =
              error instanceof Error
                ? error.message
                : t('uploadProgress.uploadFailedDefault', {
                    defaultValue: 'Upload failed',
                  });
            viableBatches[idx].forEach((entry) => failUpload(entry.storeId, errorMessage));
          }
        }
      };
      await Promise.all(
        Array.from(
          { length: Math.min(UPLOAD_BATCH_CONFIG.uploadConcurrency, viableBatches.length) },
          () => poolWorker()
        )
      );

      // All batch streams have settled — finalize the session (completes any
      // rows still in flight as a safety net).
      useUploadStore.getState().finalizeSession(uploadId);

      const sessionStoreIds = new Set(fileEntries.map((e) => e.storeId));
      const failedInSession = useUploadStore
        .getState()
        .items.filter((i) => sessionStoreIds.has(i.id) && i.status === 'failed');
      notifyUploadFailures(failedInSession, maxFileSizeMB);

      if (anySuccess) {
        await refreshData();
      }
    },
    [selectedKbId, isAllRecordsMode, allRecordsTableData, tableData, addUploadItems, startUpload, completeUpload, failUpload, refreshData, maxFileSizeBytes, maxFileSizeMB, t]
  );

  // Handle folder info click
  const handleFolderInfoClick = useCallback(() => {
    setIsFolderDetailsOpen(true);
  }, []);

  // Handle share
  const [isShareSidebarOpen, setIsShareSidebarOpen] = useState(false);
  const [sharedMembers, setSharedMembers] = useState<SharedAvatarMember[]>([]);

  const nodes = useKnowledgeBaseStore((state) => state.nodes);

  // Create the share adapter for the currently selected KB node
  const shareAdapter = useMemo(() => {
    const nodeId = selectedNode?.nodeId;
    if (!nodeId) return null;
    // Only share root KB collection nodes (app with connector KB)
    if (selectedNode?.nodeType !== 'app') return null;
    const storeNode = nodes.find((n) => n.id === nodeId);
    if (!storeNode || !isKbCollectionsHubApp(storeNode)) return null;
    return createKBShareAdapter(nodeId);
  }, [selectedNode?.nodeId, selectedNode?.nodeType, nodes]);

  // Share controls are owner-only for collections.
  const isSelectedKbOwner = !isAllRecordsMode && (tableData?.permissions?.canManagePermissions ?? false);
  const canManageSelectedKbSharing = !!shareAdapter && isSelectedKbOwner;

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
    if (!canManageSelectedKbSharing) {
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
  }, [canManageSelectedKbSharing, handleAccessRevoked, shareAdapter]);

  const getPreviewErrorMessage = useCallback((err: unknown): string => {
    if (err instanceof Error && err.message) return err.message;

    const maybeMessage = (err as { message?: unknown })?.message;
    if (typeof maybeMessage === 'string' && maybeMessage.trim()) return maybeMessage;

    const maybeStatusText = (err as { statusText?: unknown })?.statusText;
    if (typeof maybeStatusText === 'string' && maybeStatusText.trim()) return maybeStatusText;

    return 'Failed to load file';
  }, []);

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
          webUrl: item.webUrl,
        });
        setPreviewMode('sidebar');

        // 2. Fetch record details and stream file in parallel
        const streamAsPdf =
          isPresentationFile(item.mimeType, item.name) ||
          isLegacyWordDocFile(item.mimeType, item.name);
        const streamOptions = streamAsPdf ? { convertTo: 'application/pdf' } : undefined;
        const [recordDetails, blob] = await Promise.all([
          KnowledgeBaseApi.getRecordDetails(item.id),
          KnowledgeBaseApi.streamRecord(item.id, streamOptions),
        ]);

        // 3. For DOCX we hand the Blob straight through to DocxRenderer.
        //    All other renderers still expect a URL.
        const recordMime = recordDetails.record.mimeType || item.extension || '';
        const resolvedType = resolvePreviewMimeAfterStream(
          recordMime,
          item.name,
          blob,
          !!streamOptions,
        );
        const fr = recordDetails.record.fileRecord;
        const isDocx = isDocxFile(
          recordDetails.record.mimeType,
          item.name,
          recordDetails.record.recordName,
          item.extension ?? undefined,
          fr?.extension,
        );
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
          webUrl: recordDetails.record.webUrl || undefined,
          previewRenderable: recordDetails.record.previewRenderable,
        });

      } catch (error) {
        const errorMessage = getPreviewErrorMessage(error);
        console.error('Failed to load file preview:', { error, message: errorMessage });
        setPreviewFile(prev => prev ? {
          ...prev,
          error: errorMessage,
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

        const legacyStreamAsPdf =
          isPresentationFile(item.fileType, item.name) ||
          isLegacyWordDocFile(item.fileType, item.name);
        const legacyStreamOptions = legacyStreamAsPdf ? { convertTo: 'application/pdf' } : undefined;
        const [recordDetails, blob] = await Promise.all([
          KnowledgeBaseApi.getRecordDetails(item.id),
          KnowledgeBaseApi.streamRecord(item.id, legacyStreamOptions),
        ]);

        // DOCX uses the Blob directly; other types stay on URLs.
        const legacyRecordMime = recordDetails.record.mimeType || item.fileType || '';
        const resolvedType = resolvePreviewMimeAfterStream(
          legacyRecordMime,
          item.name,
          blob,
          !!legacyStreamOptions,
        );
        const frLegacy = recordDetails.record.fileRecord;
        const isDocx = isDocxFile(
          recordDetails.record.mimeType,
          item.name,
          recordDetails.record.recordName,
          undefined,
          frLegacy?.extension,
        );
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
          webUrl: recordDetails.record.webUrl || undefined,
          previewRenderable: recordDetails.record.previewRenderable,
        });

      } catch (error) {
        const errorMessage = getPreviewErrorMessage(error);
        console.error('Failed to load file preview:', { error, message: errorMessage });
        setPreviewFile(prev => prev ? {
          ...prev,
          error: errorMessage,
          isLoading: false,
        } : null);
      }
    }
  }, [getPreviewErrorMessage]);

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
          // Use clean nav URL — filters don't carry over when drilling into a new container
          setIsSearchOpen(false);
          router.push(buildCleanNavUrl(isAllRecordsMode, { nodeType: item.nodeType, nodeId: item.id }));
        } else if (item.nodeType === 'record') {
          handlePreviewFile(item);
        }
      } else {
        // Handle legacy KnowledgeBaseItem format
        if (item.type === 'folder') {
          setIsSearchOpen(false);
          setCurrentFolderId(item.id);
          // Use clean nav URL to clear search params when navigating into folders
          router.push(buildCleanNavUrl(false, { kbId: selectedKbId || '', folderId: item.id }));
        } else {
          handlePreviewFile(item);
        }
      }
    },
    [selectedKbId, router, isAllRecordsMode, setCurrentFolderId, handlePreviewFile, setIsSearchOpen]
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
        const isKbCollection = isKbCollectionNode(getReindexNodeFromHubItem({
          nodeType: hubItem.nodeType,
          connector: hubItem.connector,
          subType: hubItem.subType,
        }));

        if (isKbCollection) {
          await KnowledgeBaseApi.renameKnowledgeBase(hubItem.id, newName);
        } else if (hubItem.nodeType === 'folder' || hubItem.nodeType === 'recordGroup' || hubItem.nodeType === 'app') {
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
      const isKbCollection =
        nodeType === 'kb' ||
        (nodeType === 'app' && (!selectedKbId || nodeId === selectedKbId));

      if (isKbCollection) {
        await KnowledgeBaseApi.renameKnowledgeBase(nodeId, newName);
      } else if (nodeType === 'folder' || nodeType === 'recordGroup' || nodeType === 'app') {
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
  // Use clean nav URL so filters don't carry over when navigating up the tree
  const handleBreadcrumbClick = useCallback(
    (breadcrumb: Breadcrumb) => {
      setIsSearchOpen(false);
      if (breadcrumb.id === 'all-records-root') {
        router.push(buildCleanNavUrl(isAllRecordsMode, {}));
        return;
      }
      router.push(buildCleanNavUrl(isAllRecordsMode, { nodeType: breadcrumb.nodeType, nodeId: breadcrumb.id }));
    },
    [router, isAllRecordsMode, setIsSearchOpen]
  );

  const executeReindex = useCallback(
    async (
      item: KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem,
      depth: number,
      statusFilters?: string[],
    ) => {
      const hubItem = item as KnowledgeHubNode;
      const reindexNode = getReindexNodeFromHubItem({
        nodeType: hubItem.nodeType,
        indexingStatus: hubItem.indexingStatus,
        hasChildren: hubItem.hasChildren,
        connector: hubItem.connector,
        subType: hubItem.subType,
      });

      const toastId = toast.loading(getReindexLoadingTitle(reindexNode, depth, statusFilters), {
        icon: 'lap_timer',
      });

      try {
        if (isKbCollectionNode(reindexNode)) {
          await KnowledgeBaseApi.reindexKnowledgeBase(item.id, statusFilters);
        } else if (hubItem.nodeType === 'recordGroup') {
          await KnowledgeBaseApi.reindexRecordGroup(item.id, depth, statusFilters);
        } else if (hubItem.nodeType === 'app') {
          await KnowledgeBaseApi.reindexRecordGroup(item.id, depth, statusFilters);
        } else {
          await KnowledgeBaseApi.reindexItem(item.id, depth, statusFilters);
        }

        toast.update(toastId, {
          variant: 'success',
          title: getReindexSuccessTitle(reindexNode, depth, statusFilters),
        });

        await refreshData();
      } catch (error: unknown) {
        let errorMessage = 'Failed to start reindexing';

        if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') {
          errorMessage = error.message;
        }

        toast.update(toastId, {
          variant: 'error',
          title: errorMessage,
          action: {
            label: 'Try Again',
            icon: 'refresh',
            onClick: () => {
              toast.dismiss(toastId);
              void executeReindex(item, depth, statusFilters);
            },
          },
        });
      }
    },
    [refreshData],
  );

  const proceedWithReindex = useCallback(
    async (
      item: KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem,
      statusFilters?: string[],
    ) => {
      const hubItem = item as KnowledgeHubNode;
      const reindexNode = getReindexNodeFromHubItem({
        nodeType: hubItem.nodeType,
        indexingStatus: hubItem.indexingStatus,
        hasChildren: hubItem.hasChildren,
        connector: hubItem.connector,
        subType: hubItem.subType,
      });

      if (statusFilters?.length) {
        await executeReindex(item, FOLDER_REINDEX_DEPTH, statusFilters);
        return;
      }

      if (needsReindexScopeModal(reindexNode)) {
        setReindexScopePending({
          item: hubItem,
          primaryLabelKey: getPrimaryReindexMenuLabelKey(reindexNode),
        });
        return;
      }

      const depth = supportsBulkReindex(reindexNode) ? FOLDER_REINDEX_DEPTH : REINDEX_SELF_DEPTH;
      await executeReindex(item, depth);
    },
    [executeReindex],
  );

  const handleReindexClick = useCallback(
    async (
      item: KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem,
      statusFilters?: string[],
    ) => {
      const hubItem = item as KnowledgeHubNode;
      const reindexNode = getReindexNodeFromHubItem({
        nodeType: hubItem.nodeType,
        indexingStatus: hubItem.indexingStatus,
        hasChildren: hubItem.hasChildren,
        connector: hubItem.connector,
        subType: hubItem.subType,
      });

      if (requiresForceReindexConfirmation(reindexNode, statusFilters)) {
        setForceReindexPending({ item: hubItem, statusFilters });
        return;
      }

      await proceedWithReindex(item, statusFilters);
    },
    [proceedWithReindex],
  );

  // Bridge: consume pending sidebar actions (reindex/delete/create-collection) and open corresponding dialogs
  useEffect(() => {
    if (!pendingSidebarAction) return;
    if (pendingSidebarAction.type === 'create-collection') {
      setCreateFolderContext({ type: 'collection' });
      setIsCreateFolderDialogOpen(true);
    } else {
      const {
        type,
        nodeId,
        nodeName,
        nodeType,
        rootKbId,
        statusFilters,
        indexingStatus,
        hasChildren,
        connector,
        subType,
      } = pendingSidebarAction;
      if (type === 'reindex') {
        void handleReindexClick(
          {
            id: nodeId,
            name: nodeName,
            nodeType,
            indexingStatus,
            hasChildren,
            connector,
            subType,
          } as KnowledgeHubNode,
          statusFilters,
        );
      } else if (type === 'delete') {
        setItemToDelete({ id: nodeId, name: nodeName, nodeType, rootKbId });
        setIsDeleteDialogOpen(true);
      }
    }
    clearPendingSidebarAction();
  }, [pendingSidebarAction, clearPendingSidebarAction, handleReindexClick]);

  const handleForceReindexConfirm = useCallback(async () => {
    if (!forceReindexPending) return;
    const pending = forceReindexPending;
    setIsReindexSubmitting(true);
    try {
      await proceedWithReindex(pending.item, pending.statusFilters);
    } finally {
      setIsReindexSubmitting(false);
      setForceReindexPending(null);
    }
  }, [forceReindexPending, proceedWithReindex]);

  const handleReindexScopeConfirm = useCallback(
    async (depth: number) => {
      if (!reindexScopePending) return;
      setIsReindexSubmitting(true);
      try {
        await executeReindex(reindexScopePending.item, depth);
        setReindexScopePending(null);
      } finally {
        setIsReindexSubmitting(false);
      }
    },
    [reindexScopePending, executeReindex],
  );

  // Handle move - opens the move folder sidebar
  const handleMoveClick = useCallback((item: KnowledgeBaseItem) => {
    setItemToMove(item);
    setIsMoveDialogOpen(true);
  }, []);

  // Handle move dialog open/close with state reset
  const handleMoveDialogOpenChange = useCallback((isOpen: boolean) => {
    setIsMoveDialogOpen(isOpen);
    if (!isOpen) {
      setMoveDialogLoadingIds(new Set());
    }
  }, []);

  // Handle node expansion in move dialog - lazy load folder children
  const handleMoveDialogExpand = useCallback(
    async (nodeId: string) => {
      const {
        categorizedNodes: freshCategorized,
        nodeChildrenCache: freshCache,
        nodes: storeNodes,
      } = useKnowledgeBaseStore.getState();

      // Helper to check if node already has children loaded in tree
      const hasChildrenInTree = (tree: EnhancedFolderTreeNode[], targetId: string): boolean => {
        for (const node of tree) {
          if (node.id === targetId) return (node.children?.length ?? 0) > 0;
          if (node.children?.length && hasChildrenInTree(node.children as EnhancedFolderTreeNode[], targetId)) {
            return true;
          }
        }
        return false;
      };

      // Check if children already loaded in categorizedNodes
      if (freshCategorized) {
        const alreadyLoaded =
          hasChildrenInTree(freshCategorized.shared, nodeId) ||
          hasChildrenInTree(freshCategorized.private, nodeId);
        if (alreadyLoaded) return;
      }

      // Check cache first
      const cachedChildren = freshCache.get(nodeId);
      if (cachedChildren !== undefined) {
        const effectiveHasChildFolders = effectiveHasChildrenAfterSidebarExpand(cachedChildren);

        if (cachedChildren.length > 0) {
          addNodes(cachedChildren);
        }

        const latest = useKnowledgeBaseStore.getState();
        if (latest.categorizedNodes) {
          const parentNode = latest.nodes.find((n) => n.id === nodeId);
          if (parentNode) {
            const section = categorizeNode(parentNode);
            const updatedTree = mergeChildrenIntoTree(
              latest.categorizedNodes[section],
              nodeId,
              cachedChildren,
              effectiveHasChildFolders
            );
            setCategorizedNodes({ ...latest.categorizedNodes, [section]: updatedTree });
          }
        }
        return;
      }

      // Not cached - fetch from API
      try {
        setMoveDialogLoadingIds((prev) => new Set(prev).add(nodeId));

        const nodeInStore = storeNodes.find((n) => n.id === nodeId);
        const resolvedNodeType = (nodeInStore?.nodeType ?? 'folder') as NodeType;

        const response = await KnowledgeHubApi.getNodeChildren(resolvedNodeType, nodeId, {
          onlyContainers: true,
          sortBy: 'name',
          sortOrder: 'asc',
        });

        cacheNodeChildren(nodeId, response.items);
        addNodes(response.items);

        const effectiveHasChildFolders = effectiveHasChildrenAfterSidebarExpand(response.items);

        const latest = useKnowledgeBaseStore.getState();
        if (latest.categorizedNodes) {
          const parentNode = latest.nodes.find((n) => n.id === nodeId);
          if (parentNode) {
            const section = categorizeNode(parentNode);
            const updatedTree = mergeChildrenIntoTree(
              latest.categorizedNodes[section],
              nodeId,
              response.items,
              effectiveHasChildFolders
            );
            setCategorizedNodes({ ...latest.categorizedNodes, [section]: updatedTree });
          }
        }
      } catch (error) {
        console.error('Failed to expand node in move dialog', { nodeId, error });
      } finally {
        setMoveDialogLoadingIds((prev) => {
          const next = new Set(prev);
          next.delete(nodeId);
          return next;
        });
      }
    },
    [addNodes, cacheNodeChildren, setCategorizedNodes]
  );

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

  // Handle delete — reuses the same confirmation dialog/dispatcher as the
  // sidebar delete flow (handleSidebarDeleteConfirm) instead of a separate path.
  const handleDelete = useCallback((item: KnowledgeBaseItem) => {
    const isHubNode = 'nodeType' in item && 'origin' in item;
    // 'app'/'kb' nodes have no nodeType here — deleteNode's fallback path
    // (nodeType neither 'folder' nor 'record') routes them to
    // deleteKnowledgeBase, which is what deleting a collection needs.
    let nodeType: NodeType | undefined;
    if (isHubNode) {
      const hubNode = item as unknown as KnowledgeHubNode;
      if (hubNode.nodeType === 'record') nodeType = 'record';
      else if (hubNode.nodeType === 'folder' || hubNode.nodeType === 'recordGroup') nodeType = 'folder';
    } else {
      nodeType = item.type === 'folder' ? 'folder' : 'record';
    }
    setItemToDelete({
      id: item.id,
      name: item.name,
      nodeType,
      rootKbId: selectedKbId || undefined,
    });
    setIsDeleteDialogOpen(true);
  }, [selectedKbId]);

  // ========================================
  // Sidebar Action Handlers
  // ========================================

  // Sidebar: Delete confirm handler
  const handleSidebarDeleteConfirm = useCallback(async () => {
    if (!itemToDelete) return;
    const deletedId = itemToDelete.id;
    const deletedNodeType = itemToDelete.nodeType;
    setIsDeleting(true);
    try {
      await KnowledgeBaseApi.deleteNode({
        nodeId: deletedId,
        nodeType: deletedNodeType,
        rootKbId: itemToDelete.rootKbId,
      });
      toast.success(`"${itemToDelete.name}" deleted successfully`);
      setIsDeleteDialogOpen(false);
      setItemToDelete(null);
      await refreshDataAfterDelete([deletedId]);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { message?: string } }; message?: string };
      toast.error(
        err?.response?.data?.message ||
          `Failed to delete ${deletedNodeType === 'folder' ? 'folder' : 'collection'}`
      );
    } finally {
      setIsDeleting(false);
    }
  }, [itemToDelete, refreshDataAfterDelete]);

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
    const items = selectedItemsArray.map(item => {
      const hubItem = ('nodeType' in item) ? (item as KnowledgeHubNode) : undefined;
      return {
        id: item.id,
        name: item.name,
        nodeType: hubItem?.nodeType,
        connector: hubItem?.connector,
        subType: hubItem?.subType,
      };
    });
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
        let nodeType: 'kb' | 'app' | 'folder' | 'record' = 'record';
        if (isKnowledgeHubNodeItem) {
          const hubNode = item as KnowledgeHubNode;
          if (hubNode.nodeType === 'kb' || hubNode.nodeType === 'app') nodeType = 'app';
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

      await bulkDeleteSelected(items, refreshDataAfterDelete);
      setIsBulkDeleteDialogOpen(false);
    } finally {
      setIsBulkDeleting(false);
    }
  }, [selectedItemsArray, selectedKbId, bulkDeleteSelected, refreshDataAfterDelete]);

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
      } else if (allRecordsSidebarSelection.type === 'explorer') {
        return allRecordsTableData?.currentNode?.name || 'All Records';
      }
      return 'All';
    }
    return tableData?.currentNode?.name || 'Collections';
  }, [
    isAllRecordsMode,
    allRecordsSidebarSelection,
    tableData,
    allRecordsSearchQuery,
    searchQuery,
    allRecordsTableData?.currentNode?.name,
  ]);

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
        {/* KB Header - mode-aware; shown in collections mode when browsing a node,
            listing all collections, or when filters/search are active (even with 0 results) */}
        {(isAllRecordsMode
          ? (allRecordsBreadcrumbs && allRecordsBreadcrumbs.length > 0)
          : ((nodeId && tableData?.breadcrumbs) ||
            (!nodeId &&
              hasCollections &&
              ((tableData?.items?.length ?? 0) > 0 || hasActiveFilters || hasSearchQuery)))) && (
          <>
            <Header
              pageViewMode={pageViewMode}
              breadcrumbs={isAllRecordsMode ? allRecordsBreadcrumbs : tableData?.breadcrumbs}
              currentTitle={currentTitle}
              currentNodeSyncStatus={currentNodeSyncStatus}
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
                !isAllRecordsMode && nodeId && tableData?.permissions?.canEdit !== false
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
          showCheckbox={!(isAllRecordsMode && (allRecordsSidebarSelection.type === 'all' || allRecordsSidebarSelection.type === 'connector'))}
          hasActiveFilters={hasActiveFilters}
          hasSearchQuery={hasSearchQuery}
          hasCollections={hasCollections}
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
            !isAllRecordsMode
              ? handleRename
              : undefined
          }
          onReindex={handleReindexClick}
          onReplace={!isAllRecordsMode ? (item) => handleReplaceClick(item as KnowledgeHubNode) : undefined}
          onMove={!isAllRecordsMode ? handleMoveClick : undefined}
          onDelete={!isAllRecordsMode ? handleDelete : undefined}
          onDownload={handleDownload}
          onCreateFolder={isAllRecordsMode ? undefined : handleCreateFolder}
          onUpload={isAllRecordsMode ? undefined : handleUpload}
          onGoToCollection={handleGoToCollection}
          refreshData={refreshDataAfterDelete}
        />

        {/* Selection Action Bar - shows when items are selected.
            Hidden in All Records "All" and connector views (mirrors the checkbox guard
            above). Rows in those views are top-level aggregates that aren't individually
            reindexable, and stale selections from a prior view shouldn't surface here. */}
        {!(isAllRecordsMode && (allRecordsSidebarSelection.type === 'all' || allRecordsSidebarSelection.type === 'connector')) && (
          <SelectionActionBar
            selectedCount={selectedCount}
            onDeselectAll={handleDeselectAll}
            onChat={handleBulkChat}
            onReindex={handleBulkReindex}
            onDelete={handleBulkDeleteClick}
            pageViewMode={isAllRecordsMode ? 'all-records' : 'collections'}
          />
        )}

        {/* Chat Bar (temporarily disabled)
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
        } */}
      </Flex>

      {/* Force reindex confirmation (table, grid, and sidebar) */}
      <ConfirmationDialog
        open={forceReindexPending !== null}
        onOpenChange={(open) => {
          if (!open && !isReindexSubmitting) setForceReindexPending(null);
        }}
        title={t('forceReindexConfirm.title', { defaultValue: 'Start force reindex?' })}
        message={t('forceReindexConfirm.message', {
          defaultValue:
            'This re-indexes the document from scratch and may incur extra cost. Use this when search results are stale, or the document is not searchable even after indexing is complete.',
        })}
        confirmLabel={t('forceReindexConfirm.confirm', { defaultValue: 'Confirm' })}
        cancelLabel={t('common.cancel', { defaultValue: 'Cancel' })}
        confirmVariant="primary"
        isLoading={isReindexSubmitting}
        onConfirm={() => void handleForceReindexConfirm()}
      />

      {reindexScopePending && (
        <ReindexScopeDialog
          open
          onOpenChange={(open) => {
            if (!open && !isReindexSubmitting) setReindexScopePending(null);
          }}
          itemName={reindexScopePending.item.name}
          primaryLabelKey={reindexScopePending.primaryLabelKey}
          onConfirm={handleReindexScopeConfirm}
          isSubmitting={isReindexSubmitting}
        />
      )}

      {itemToDelete && (
        <DeleteConfirmationDialog
          open={isDeleteDialogOpen}
          onOpenChange={(open) => {
            setIsDeleteDialogOpen(open);
            if (!open) setItemToDelete(null);
          }}
          onConfirm={handleSidebarDeleteConfirm}
          itemName={itemToDelete.name}
          itemType={
            itemToDelete.nodeType === 'folder'
              ? 'folder'
              : itemToDelete.nodeType === 'record'
                ? 'record'
                : 'KB'
          }
          isDeleting={isDeleting}
        />
      )}

      {/* Collections-only dialogs */}
      {!isAllRecordsMode && (
        <>
          {/* Move Folder Sidebar */}
          <MoveFolderSidebar
            open={isMoveDialogOpen}
            onOpenChange={handleMoveDialogOpenChange}
            currentFolderId={storeFolderId}
            collectionTree={moveCollectionTree}
            itemToMoveId={itemToMove?.id}
            onMove={handleMoveConfirm}
            isMoving={isMoving}
            onExpand={handleMoveDialogExpand}
            loadingNodeIds={moveDialogLoadingIds}
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
            ...(previewFile.webUrl ? { webUrl: previewFile.webUrl } : {}),
            previewRenderable: previewFile.previewRenderable,
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
            ...(previewFile.webUrl ? { webUrl: previewFile.webUrl } : {}),
            previewRenderable: previewFile.previewRenderable,
          }}
          isLoading={previewFile.isLoading}
          error={previewFile.error}
          recordDetails={previewFile.recordDetails}
          onExitFullscreen={() => setPreviewMode('sidebar')}
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
