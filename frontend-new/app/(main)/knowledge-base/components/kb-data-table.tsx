'use client';

import React, { useState } from 'react';
import { Flex, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useKnowledgeBaseStore } from '../store';
import { DeleteConfirmationDialog } from './dialogs'; 
import { KbListView } from './kb-list-view';
import { KbGridView } from './kb-grid-view';
import type {
  KnowledgeBaseItem,
  KnowledgeHubNode,
  NodePermissions,
  AllRecordItem,
  PageViewMode,
} from '../types';
import { AlertSquareIcon, EmptyIcon, NotFoundIcon } from '@/app/components/ui';

// Union type for items that can be displayed
type TableItem = KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem;

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
    page: number;
    limit: number;
    totalItems: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
  permissions?: NodePermissions;
  currentNodeName?: string;
  pageViewMode?: PageViewMode;
  showSourceColumn?: boolean;
  hasActiveFilters?: boolean;
  hasSearchQuery?: boolean;
  onRefresh?: () => void;
  onPageChange?: (page: number) => void;
  onLimitChange?: (limit: number) => void;
  onItemClick: (item: TableItem) => void;
  onPreview?: (item: TableItem) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
  onCreateFolder?: () => void;
  onUpload?: () => void;
  onGoToCollection?: () => void;
  refreshData?: () => Promise<void>;
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
  hasActiveFilters = false,
  hasSearchQuery = false,
  onRefresh,
  onPageChange,
  onLimitChange,
  onItemClick,
  onPreview,
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
}: KbDataTableProps) {
  const { selectedItems, toggleItemSelection, selectItem, clearSelection, selectedRecords, toggleRecordSelection, selectRecord, clearRecordSelection, deleteNode, deletingNodeIds, viewMode, sort, setSort, allRecordsSort, setAllRecordsSort, tableData: storeTableData, allRecordsSidebarSelection, isLoadingFlatCollections, loadingAppIds, appNodes } =
    useKnowledgeBaseStore();

  // Collections in the sidebar are still being fetched (initial load).
  // Used to show a spinner in the center "No collection selected" empty state
  // while the user is waiting for the sidebar to populate.
  const kbApp = appNodes.find((n) => n.connector === 'KB');
  const isLoadingCollections =
    isLoadingFlatCollections || (kbApp ? loadingAppIds.has(kbApp.id) : false);

  const isAllRecords = pageViewMode === 'all-records';
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

    const isFolder = isKnowledgeHubNode(itemToDelete)
      ? ['kb', 'app', 'folder', 'recordGroup'].includes(itemToDelete.nodeType)
      : itemToDelete.type === 'folder';

    const nodeType = isKnowledgeHubNode(itemToDelete)
      ? itemToDelete.nodeType === 'kb'
        ? 'kb'
        : isFolder
        ? 'folder'
        : 'record'
      : itemToDelete.type === 'folder'
      ? 'folder'
      : 'record';

    // Get KB ID - we need it for folder deletion
    const storeBreadcrumbs = useKnowledgeBaseStore.getState().tableData?.breadcrumbs;
    const kbId = storeBreadcrumbs && storeBreadcrumbs.length > 1
      ? storeBreadcrumbs[1].id
      : isKnowledgeHubNode(itemToDelete) && 'parentId' in itemToDelete
      ? itemToDelete.parentId
      : undefined;

    try {
      await deleteNode(itemToDelete.id, nodeType, kbId || undefined, refreshData);
      setDeleteDialogOpen(false);
      setItemToDelete(null);
    } catch (error) {
      // Error is handled in the store
      console.error('Delete failed:', error);
    }
  };

  const getWarningMessage = (item: TableItem): string | undefined => {    
    const isFolder = isKnowledgeHubNode(item)
      ? ['kb', 'app', 'folder', 'recordGroup'].includes(item.nodeType)
      : item.type === 'folder';

    if (isFolder && item.hasChildren) {
      // TODO: Get actual counts from API or state
      return 'You have 4 Collections, 35 Files inside this collection folder';
    }
    return undefined;
  };

  // Show refreshing state
  if (isRefreshing) {
    return (
      <Flex align="center" justify="center" style={{ flex: 1 }}>
        <LottieLoader variant="loader" size={32} showLabel label="Refreshing..." />
      </Flex>
    );
  }

  // Show loading state
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ flex: 1, backgroundColor: 'var(--olive-2)' }}>
        <LottieLoader variant="loader" size={32} showLabel />
      </Flex>
    );
  }

  // Show error state
  if (error) {
    return (
      <Flex align="center" justify="center" direction="column" gap="3" style={{ flex: 1 }}>
        <AlertSquareIcon size={56} color="var(--red-11)" />
        <Text size="2" color="red">{error}</Text>
        {onRefresh && (
          <Button onClick={onRefresh} variant="soft" size="2">
            <MaterialIcon name="refresh" size={16} />
            Retry
          </Button>
        )}
      </Flex>
    );
  }

  // Show empty state when no node selected (no breadcrumbs) - Collections mode only
  const hasBreadcrumbs = !!storeTableData?.breadcrumbs?.length;
  if (items.length === 0 && !hasBreadcrumbs && pageViewMode === 'collections') {
    // While the sidebar's collections list is still being fetched, show a
    // loading spinner rather than the "No collection selected" empty state,
    // so the user doesn't think they have no collections while the API is
    // still responding.
    if (isLoadingCollections) {
      return (
        <Flex align="center" justify="center" style={{ flex: 1, backgroundColor: 'var(--olive-2)' }}>
          <LottieLoader variant="loader" size={32} showLabel />
        </Flex>
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
            No collection selected
          </Text>
          <Text size="2" style={{ color: 'var(--slate-10)' }}>
            Select a collection from the sidebar or create a new one
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
              Create Collection
            </Button>
          )}
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex direction="column" style={{ flex: 1, overflow: 'hidden', backgroundColor: 'var(--olive-2)' }}>
      {/* Conditional view rendering based on viewMode */}
      {items.length === 0 ? (
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
                ? 'No results found'
                : pageViewMode === 'all-records'
                  ? allRecordsSidebarSelection?.type === 'collection'
                    ? `${allRecordsSidebarSelection.name} is empty`
                    : 'No records found'
                  : currentNodeName
                    ? `${currentNodeName} is empty`
                    : 'This folder is empty'
              }
            </Text>
            <Text size="2" style={{ color: 'var(--slate-10)' }}>
              {hasSearchQuery
                ? "Couldn't find what you are searching for. Try searching for something else."
                : hasActiveFilters
                  ? 'Select a different source or adjust your filters'
                  : pageViewMode === 'all-records'
                    ? allRecordsSidebarSelection?.type === 'collection'
                      ? 'You can add your company files or folders from the Collections'
                      : 'Select a different source or adjust your filters'
                    : permissions?.canCreateFolders ? 'Add your files or folders' : ''
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
                  Create Folder
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
                  Upload
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
                Go to Collections
              </Button>
            </Flex>
          )}
        </Flex>
      ) : viewMode === 'grid' ? (
        <KbGridView
          items={items}
          selectedItems={activeSelectedItems}
          pagination={pagination}
          onPageChange={onPageChange}
          onLimitChange={onLimitChange}
          onSelectItem={activeToggleSelection}
          onItemClick={onItemClick}
          onPreview={onPreview}
          onRename={onRename}
          onReindex={onReindex}
          onReplace={onReplace}
          onMove={onMove}
          onDelete={handleDeleteClick}
          onDownload={onDownload}
        />
      ) : (
        <KbListView
          items={items}
          selectedItems={activeSelectedItems}
          allSelected={allSelected}
          showSourceColumn={showSourceColumn}
          sort={isAllRecords ? allRecordsSort : sort}
          pagination={pagination}
          onPageChange={onPageChange}
          onLimitChange={onLimitChange}
          onSelectAll={handleSelectAll}
          onSort={isAllRecords ? setAllRecordsSort : setSort}
          onSelectItem={activeToggleSelection}
          onItemClick={onItemClick}
          onPreview={onPreview}
          onRename={onRename}
          onReindex={onReindex}
          onReplace={onReplace}
          onMove={onMove}
          onDelete={handleDeleteClick}
          onDownload={onDownload}
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
