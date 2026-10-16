'use client';

import { useDemoDataActive } from '@/app/(main)/workspace/connectors/demo-data/use-demo-data';
import React, { useState } from 'react';
import { Box, Flex, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { TableSkeleton } from '@/app/components/data-display/table-skeleton';
import { useKnowledgeBaseStore } from '../store';
import { useTranslation } from 'react-i18next';
import { DeleteConfirmationDialog } from './dialogs'; 
import { KbGridView, KbListView, useKbListExtraColumns } from '@/config';
import type {
  KnowledgeBaseItem,
  KnowledgeHubNode,
  NodePermissions,
  AllRecordItem,
  PageViewMode,
} from '../types';
import { AlertSquareIcon, EmptyIcon, NotFoundIcon } from '@/app/components/ui';
import { resolveRootKbIdFromContext } from '../utils/resolve-root-kb-id';
import { TableHeaderCell } from './kb-list-view';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

// Union type for items that can be displayed
type TableItem = KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem;

const NO_ITEMS: TableItem[] = [];

// Type guard to check if item is KnowledgeHubNode
function isKnowledgeHubNode(item: TableItem): item is KnowledgeHubNode {
  return 'nodeType' in item && 'origin' in item;
}

interface KbDataTableProps {
  items: TableItem[];
  isLoading?: boolean;
  isRefreshing?: boolean;
  error?: string | null;
  pagination?: {
    limit: number;
    totalItems: number;
    startIndex?: number;
    endIndex?: number;
    hasNext: boolean;
    hasPrev: boolean;
    nextCursor?: string | null;
    prevCursor?: string | null;
  };
  permissions?: NodePermissions;
  currentNodeName?: string;
  pageViewMode?: PageViewMode;
  showSourceColumn?: boolean;
  showCheckbox?: boolean;
  hasActiveFilters?: boolean;
  hasSearchQuery?: boolean;
  hasCollections?: boolean;
  extrasRefreshKey?: number;
  onRefresh?: () => void;
  onCursorChange?: (cursor: string | null, direction?: 'next' | 'prev') => void;
  onLimitChange?: (limit: number) => void;
  onItemClick: (item: TableItem) => void;
  onPreview?: (item: TableItem) => void;
  onGoToParent?: (item: KnowledgeHubNode) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem, statusFilters?: string[]) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
  onCreateFolder?: () => void;
  onUpload?: () => void;
  onGoToCollection?: () => void;
  refreshData?: (deletedIds?: string[]) => Promise<void>;
  /** False when no collection is open (a search at the Collections root): see `needsCollectionContext`. */
  hasCollectionContext?: boolean;
}

export function KbDataTable({
  items,
  isLoading = false,
  isRefreshing = false,
  error = null,
  pagination,
  permissions,
  currentNodeName,
  pageViewMode = 'collections',
  showSourceColumn = false,
  showCheckbox = true,
  hasActiveFilters = false,
  hasSearchQuery = false,
  hasCollections = false,
  extrasRefreshKey = 0,
  onRefresh,
  onCursorChange,
  onLimitChange,
  onItemClick,
  onPreview,
  onGoToParent,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete: _onDelete,
  onDownload,
  onCreateFolder,
  onUpload,
  onGoToCollection,
  refreshData,
  hasCollectionContext = true,
}: KbDataTableProps) {
  const { t } = useTranslation();
  // Looked up here, not in the list or grid: those unmount behind the loader on
  // every navigation, which would ask for the connectors again on every click.
  useDemoDataActive();
  const { selectedItems, toggleItemSelection, selectItem, clearSelection, selectedRecords, toggleRecordSelection, selectRecord, clearRecordSelection, deleteNode, deletingNodeIds, viewMode, sort, setSort, allRecordsSort, setAllRecordsSort, tableData: storeTableData, allRecordsSidebarSelection, isLoadingFlatCollections, loadingAppIds, appNodes } =
    useKnowledgeBaseStore();

  // Collections in the sidebar are still being fetched (initial load).
  // Used to show a spinner in the center "No collection selected" empty state
  // while the user is waiting for the sidebar to populate.
  const kbApp = appNodes.find((n) => n.connector === 'KB');
  const isLoadingCollections =
    isLoadingFlatCollections || (kbApp ? loadingAppIds.has(kbApp.id) : false);

  const isAllRecords = pageViewMode === 'all-records';
  const extraColumns = useKbListExtraColumns(items, extrasRefreshKey);
  const activeSelectedItems = isAllRecords ? selectedRecords : selectedItems;
  const activeToggleSelection = isAllRecords ? toggleRecordSelection : toggleItemSelection;

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [itemToDelete, setItemToDelete] = useState<TableItem | null>(null);

  const allSelected = items.length > 0 && items.every((item) => activeSelectedItems.has(item.id));
  const _someSelected = items.some((item) => activeSelectedItems.has(item.id));

  const handleSelectAll = () => {
    if (allSelected) {
      if (isAllRecords) {
        clearRecordSelection();
      } else {
        clearSelection();
      }
    } else {
      // Select all current items from the items prop (not from store.items)
      items.forEach((item) => {
        if (isAllRecords) {
          selectRecord(item.id);
        } else {
          selectItem(item.id);
        }
      });
    }
  };

  const handleDeleteClick = (item: TableItem) => {
    setItemToDelete(item);
    setDeleteDialogOpen(true);
  };

  const handleDeleteConfirm = async () => {
    if (!itemToDelete) return;

    const storeState = useKnowledgeBaseStore.getState();
    const kbId = resolveRootKbIdFromContext({
      breadcrumbs: storeState.tableData?.breadcrumbs,
      categorizedNodes: storeState.categorizedNodes,
      parentId:
        isKnowledgeHubNode(itemToDelete) && 'parentId' in itemToDelete
          ? itemToDelete.parentId
          : undefined,
      nodeId: itemToDelete.id,
    });

    const isFolder = isKnowledgeHubNode(itemToDelete)
      ? ['kb', 'app', 'folder', 'recordGroup'].includes(itemToDelete.nodeType)
      : itemToDelete.type === 'folder';

    const isRootCollection =
      isKnowledgeHubNode(itemToDelete) &&
      itemToDelete.nodeType === 'recordGroup' &&
      kbId === itemToDelete.id;

    const nodeType = isKnowledgeHubNode(itemToDelete)
      ? itemToDelete.nodeType === 'record'
        ? 'record'
        : itemToDelete.nodeType === 'app' || itemToDelete.nodeType === 'kb' || isRootCollection
        ? 'app'
        : ['folder', 'recordGroup'].includes(itemToDelete.nodeType)
        ? 'folder'
        : 'record'
      : itemToDelete.type === 'folder'
      ? 'folder'
      : 'record';

    try {
      await deleteNode(itemToDelete.id, nodeType, kbId || undefined, refreshData);
      setDeleteDialogOpen(false);
      setItemToDelete(null);
    } catch (error: unknown) {
      // Error toast is already handled in the store action.
      // Close the dialog on permission-denied responses so users aren't stuck
      // with an action they cannot perform.
      const err = error as {
        statusCode?: number;
        response?: { status?: number; data?: { message?: string } };
        message?: string;
      };
      const status = err.statusCode ?? err.response?.status;
      const message = (err.response?.data?.message ?? err.message ?? '').toLowerCase();
      const isPermissionDenied = status === 403 || message.includes('permission');
      if (isPermissionDenied) {
        setDeleteDialogOpen(false);
        setItemToDelete(null);
      }
    }
  };

  const getWarningMessage = (_item: TableItem): string | undefined => undefined;
  const isMobile = useIsMobile();

  const showSkeleton = isRefreshing || isLoading;
  // The list keeps its header and shimmers its own body; the grid has no row skeleton.
  if (showSkeleton && viewMode === 'grid') {
    return (
      <RecordsLoadingTable
        label={isRefreshing ? t('common.refreshing') : t('common.loading', { defaultValue: 'Loading' })}
        showSourceColumn={showSourceColumn}
        isMobile={isMobile}
      />
    );
  }

  // Show error state
  if (error && !showSkeleton) {
    return (
      <Flex align="center" justify="center" direction="column" gap="3" style={{ flex: 1 }}>
        <AlertSquareIcon size={56} color="var(--red-11)" />
        <Text size="2" color="red">{error}</Text>
        {onRefresh && (
          <Button onClick={onRefresh} variant="soft" size="2">
            <MaterialIcon name="refresh" size={16} />
            {t('action.tryAgain')}
          </Button>
        )}
      </Flex>
    );
  }

  // Show empty state when no node selected (no breadcrumbs) - Collections mode only
  const hasBreadcrumbs = !!storeTableData?.breadcrumbs?.length;
  if (
    !showSkeleton &&
    items.length === 0 &&
    !hasBreadcrumbs &&
    pageViewMode === 'collections' &&
    !hasActiveFilters &&
    !hasSearchQuery
  ) {
    // While the sidebar's collections list is still being fetched, show a
    // loading spinner rather than the "No collection selected" empty state,
    // so the user doesn't think they have no collections while the API is
    // still responding.
    if (isLoadingCollections) {
      return (
        <RecordsLoadingTable
          label={t('common.loading', { defaultValue: 'Loading' })}
          showSourceColumn={false}
          isMobile={isMobile}
        />
      );
    }

    return (
      <Flex
        align="center"
        justify="center"
        direction="column"
        gap="4"
        style={{ flex: 1,  backgroundColor: 'var(--olive-2)' }}
      >
        {/* Heading and subtitle */}
        <Flex direction="column" align="center" gap="1">
          <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {hasCollections
              ? t('sidebar.noCollectionSelected')
              : t('sidebar.noCollectionsAvailable')}
          </Text>
          <Text size="2" style={{ color: 'var(--slate-10)' }}>
            {hasCollections
              ? t('sidebar.selectCollectionOrCreate')
              : t('sidebar.noCollectionsCreatePrompt')}
          </Text>
        </Flex>

        {/* Action buttons */}
        <Flex gap="2" align="center">
          {onCreateFolder && (
            <Button
              variant="outline"
              color="gray"
              size="2"
              onClick={onCreateFolder}
            >
              <MaterialIcon name="create_new_folder" size={16} />
              {t('dialog.createCollection')}
            </Button>
          )}
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex direction="column" style={{ flex: 1, overflow: 'hidden', background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))' }}>
      {/* Conditional view rendering based on viewMode */}
      {items.length === 0 && !showSkeleton ? (
        <Flex
          align="center"
          justify="center"
          direction="column"
          gap="4"
          style={{ minHeight: '400px', flex: 1 }}
        >
          {/* Dynamic heading based on mode, filters, and current node name */}
          <Flex direction="column" align="center" gap="1">
            {!hasSearchQuery && !hasActiveFilters && <EmptyIcon size={56} color="var(--slate-12)" />}
            {(hasSearchQuery || hasActiveFilters) && <NotFoundIcon size={56} color="var(--slate-12)" />}
            <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {hasActiveFilters || hasSearchQuery
                ? t('message.noResults')
                : pageViewMode === 'all-records'
                  ? allRecordsSidebarSelection?.type === 'collection'
                    ? t('kb.namedEmpty', { name: allRecordsSidebarSelection.name })
                    : t('kb.noRecords')
                  : currentNodeName
                    ? t('kb.namedEmpty', { name: currentNodeName })
                    : t('kb.folderEmpty')
              }
            </Text>
            <Text size="2" style={{ color: 'var(--slate-10)' }}>
              {hasSearchQuery
                ? t('kb.searchEmptyHint')
                : hasActiveFilters
                  ? t('kb.adjustFiltersHint')
                  : pageViewMode === 'all-records'
                    ? allRecordsSidebarSelection?.type === 'collection'
                      ? t('kb.addFromCollectionsHint')
                      : t('kb.adjustFiltersHint')
                    : permissions?.canCreateFolders ? t('kb.addFilesHint') : ''
              }
            </Text>
          </Flex>

          {/* Action buttons - only show in collections mode when not filtered */}
          {pageViewMode === 'collections' && hasBreadcrumbs && !hasActiveFilters && !hasSearchQuery && (
            <Flex gap="2" align="center">
              {/* Show Create Folder if callback is provided and not explicitly denied */}
              {onCreateFolder && (permissions?.canCreateFolders !== false) && (
                <Button
                  variant="outline"
                  color="gray"
                  size="2"
                  onClick={onCreateFolder}
                >
                  <MaterialIcon name="create_new_folder" size={16} />
                  {t('kb.createFolder')}
                </Button>
              )}
              {/* Show Upload if callback is provided and not explicitly denied */}
              {onUpload && (permissions?.canUpload !== false) && (
                <Button
                  variant="solid"
                  size="2"
                  style={{
                    backgroundColor: 'var(--accent-9)',
                    color: 'white'
                  }}
                  onClick={onUpload}
                >
                  <MaterialIcon name="upload" size={16} />
                  {t('action.upload')}
                </Button>
              )}
            </Flex>
          )}

          {/* All Records collection empty state button */}
          {pageViewMode === 'all-records' &&
           !hasActiveFilters &&
           !hasSearchQuery &&
           allRecordsSidebarSelection?.type === 'collection' &&
           onGoToCollection && (
            <Flex gap="2" align="center">
              <Button
                variant="solid"
                size="2"
                style={{
                  backgroundColor: 'var(--accent-9)',
                  color: 'white'
                }}
                onClick={onGoToCollection}
              >
                {t('kb.goToCollections')}
              </Button>
            </Flex>
          )}
        </Flex>
      ) : viewMode === 'grid' ? (
        <KbGridView
          items={items}
          selectedItems={activeSelectedItems}
          showCheckbox={showCheckbox}
          pagination={pagination}
          onCursorChange={onCursorChange}
          onLimitChange={onLimitChange}
          onSelectItem={activeToggleSelection}
          onItemClick={onItemClick}
          onPreview={onPreview}
          onRename={onRename}
          onReindex={onReindex}
          onReplace={onReplace}
          onMove={onMove}
          onDelete={_onDelete ? handleDeleteClick : undefined}
          onDownload={onDownload}
          hasCollectionContext={hasCollectionContext}
        />
      ) : (
        <KbListView
          items={showSkeleton ? NO_ITEMS : items}
          loading={showSkeleton}
          selectedItems={activeSelectedItems}
          allSelected={!showSkeleton && allSelected}
          showSourceColumn={showSourceColumn}
          showCheckbox={showCheckbox}
          extraColumns={extraColumns}
          sort={isAllRecords ? allRecordsSort : sort}
          pagination={showSkeleton ? undefined : pagination}
          onCursorChange={onCursorChange}
          onLimitChange={onLimitChange}
          onSelectAll={handleSelectAll}
          onSort={isAllRecords ? setAllRecordsSort : setSort}
          onSelectItem={activeToggleSelection}
          onItemClick={onItemClick}
          onPreview={onPreview}
          onGoToParent={hasSearchQuery || hasActiveFilters ? onGoToParent : undefined}
          onRename={onRename}
          onReindex={onReindex}
          onReplace={onReplace}
          onMove={onMove}
          onDelete={_onDelete ? handleDeleteClick : undefined}
          onDownload={onDownload}
          hasCollectionContext={hasCollectionContext}
        />
      )}

      {/* Delete Confirmation Dialog */}
      {itemToDelete && (
        <DeleteConfirmationDialog
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
          onConfirm={handleDeleteConfirm}
          itemName={itemToDelete.name}
          itemType={
            isKnowledgeHubNode(itemToDelete)
              ? itemToDelete.nodeType === 'kb'
                ? 'KB'
                : ['kb', 'app', 'folder', 'recordGroup'].includes(itemToDelete.nodeType)
                ? 'folder'
                : 'record'
              : itemToDelete.type === 'folder'
              ? 'folder'
              : 'record'
          }
          warningMessage={getWarningMessage(itemToDelete)}
          isDeleting={deletingNodeIds.has(itemToDelete.id)}
        />
      )}
    </Flex>
  );
}

function RecordsLoadingTable({
  label,
  showSourceColumn,
  isMobile,
}: {
  label: string;
  showSourceColumn: boolean;
  isMobile: boolean;
}) {
  return (
    <Flex
      direction="column"
      role="status"
      aria-busy="true"
      aria-label={label}
      style={{ flex: 1, minHeight: 0, backgroundColor: 'var(--olive-1)' }}
    >
      <Flex
        align="center"
        style={{
          height: 36,
          borderBottom: '1px solid var(--olive-3)',
          backgroundColor: 'var(--olive-2)',
          flexShrink: 0,
        }}
      >
        <Box style={{ width: 36, flexShrink: 0 }} />
        <TableHeaderCell label="File Name" flex={1} />
        <TableHeaderCell label="Status" width="60px" />
        {showSourceColumn && <TableHeaderCell label="Source" width="60px" />}
        {!isMobile && <TableHeaderCell label="Size" width="89px" />}
        {!isMobile && <TableHeaderCell label="Created" width="147px" />}
        {!isMobile && <TableHeaderCell label="Updated" width="146px" />}
        <Box style={{ width: 104, flexShrink: 0 }} />
      </Flex>
      <Box style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <TableSkeleton
          rows={18}
          rowHeight={60}
          actionsWidth={104}
          columns={[
            { barWidth: 0.62 },
            { width: '60px', barWidth: 0.45 },
            ...(showSourceColumn ? [{ width: '60px', barWidth: 0.45 }] : []),
            ...(!isMobile
              ? [
                  { width: '89px', barWidth: 0.55 },
                  { width: '147px', barWidth: 0.7 },
                  { width: '146px', barWidth: 0.7 },
                ]
              : []),
          ]}
        />
      </Box>
    </Flex>
  );
}
