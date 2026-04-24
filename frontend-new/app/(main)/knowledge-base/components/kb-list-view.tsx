'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Flex, Box, Text, Checkbox, Button, DropdownMenu, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { formatSize, formatDate } from '@/lib/utils/formatters';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ItemActionMenu } from './item-action-menu';
import type {
  KnowledgeBaseItem,
  KnowledgeHubNode,
  SortField,
  SortOrder,
  SortConfig,
  AllRecordItem,
  AllRecordsSortConfig,
} from '../types';
import { FolderIcon } from '@/app/components/ui';
import { KbNodeNameIcon } from '../utils/kb-node-name-icon';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import { LapTimerIcon } from '@/app/components/ui/lap-timer-icon';
import {
  runItemMenuOpenFromMenu,
  shouldHideIndexingStatusForHubRecord,
} from '../utils/kb-table-item-actions';

// Union type for items that can be displayed
type TableItem = KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem;

// Type guard to check if item is KnowledgeHubNode
function isKnowledgeHubNode(item: TableItem): item is KnowledgeHubNode {
  return 'nodeType' in item && 'origin' in item;
}

/** All Records rows extended with sourceName/sourceType from getSourceDisplay */
function isAllRecordDisplayRow(item: TableItem): item is AllRecordItem {
  return 'sourceName' in item && 'sourceType' in item;
}

interface TableHeaderCellProps {
  label: string;
  field?: SortField;
  sortable?: boolean;
  width?: string;
  flex?: number;
  sort: SortConfig | AllRecordsSortConfig;
  onSort: (config: SortConfig | AllRecordsSortConfig) => void;
}

function TableHeaderCell({
  label,
  field,
  sortable,
  width,
  flex,
  sort,
  onSort,
}: TableHeaderCellProps) {
  const isActive = sortable && field && sort.field === field;

  const handleClick = () => {
    if (sortable && field) {
      const newOrder: SortOrder =
        sort.field === field && sort.order === 'asc' ? 'desc' : 'asc';
      onSort({ field, order: newOrder } as SortConfig | AllRecordsSortConfig);
    }
  };

  if (sortable) {
    return (
      <Button
        variant="ghost"
        size="1"
        color="gray"
        onClick={handleClick}
        style={{
          width,
          flex,
          justifyContent: 'flex-start',
          padding: '0 var(--space-2)',
          cursor: 'pointer',
          backgroundColor: 'transparent'
        }}
      >
        <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
          {label}
        </Text>
        <MaterialIcon
          name={
            isActive
              ? sort.order === 'asc'
                ? 'arrow_drop_up'
                : 'arrow_drop_down'
              : 'arrow_drop_down'
          }
          size={16}
          color={'var(--emerald-11)'}
        />
      </Button>
    );
  }

  return (
    <Flex
      align="center"
      gap="1"
      style={{
        width,
        flex,
        padding: '0 var(--space-2)',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--slate-9)' }}>
        {label}
      </Text>
    </Flex>
  );
}

interface TableRowProps {
  item: TableItem;
  isSelected: boolean;
  showSourceColumn?: boolean;
  isMobile: boolean;
  onSelect: () => void;
  onClick: () => void;
  onOpen: () => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
}

function TableRow({
  item,
  isSelected,
  showSourceColumn,
  isMobile,
  onSelect,
  onClick,
  onOpen,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete,
  onDownload,
}: TableRowProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);
  const [inputWidth, setInputWidth] = useState<number | undefined>(undefined);
  const measureRef = useRef<HTMLSpanElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);

  // Determine if item is a file (record) for extension preservation
  const isFile = isKnowledgeHubNode(item)
    ? item.nodeType === 'record'
    : item.type === 'file';

  // Split name into base and extension for files
  const getNameParts = (name: string) => {
    if (!isFile) return { baseName: name, extension: '' };
    const lastDot = name.lastIndexOf('.');
    if (lastDot <= 0) return { baseName: name, extension: '' };
    return { baseName: name.substring(0, lastDot), extension: name.substring(lastDot) };
  };

  // Measure text width using a hidden span
  const measureTextWidth = (text: string) => {
    if (measureRef.current) {
      measureRef.current.textContent = text || ' ';
      return measureRef.current.offsetWidth;
    }
    return undefined;
  };

  useEffect(() => {
    if (isEditing && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
      // Measure initial text width
      const width = measureTextWidth(editValue);
      if (width !== undefined) {
        setInputWidth(width + 24); // 12px padding + border buffer
      }
    }
  }, [isEditing]);

  const startEditing = () => {
    const { baseName } = getNameParts(item.name);
    setEditValue(baseName);
    setIsEditing(true);
  };

  const handleRenameSave = async () => {
    const trimmed = editValue.trim();
    const { baseName, extension } = getNameParts(item.name);
    if (!trimmed || trimmed === baseName) {
      setIsEditing(false);
      return;
    }
    const newName = trimmed + extension;
    try {
      await onRename!(item, newName);
    } catch {
      // Error toast handled by parent; revert
    }
    setIsEditing(false);
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      handleRenameSave();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      setIsEditing(false);
    }
  };

  // Determine if item is a folder/container (all navigable container types)
  const isFolder = isKnowledgeHubNode(item)
    ? ['kb', 'app', 'folder', 'recordGroup'].includes(item.nodeType)
    : item.type === 'folder';

  // Status label for tooltip
  const getStatusLabel = (): string => {
    if (shouldHideIndexingStatusForHubRecord(item)) {
      return '';
    }
    const appendReason = (base: string, reason?: string | null, showReason = true): string =>
      showReason && reason?.trim() ? `${base} - ${reason.trim()}` : base;

    if (isKnowledgeHubNode(item)) {
      // No status from API — do not imply "Queued"
      if (item.indexingStatus == null) {
        return '';
      }
      let baseLabel: string;
      let showReason = true;
      switch (item.indexingStatus) {
        case 'COMPLETED': baseLabel = 'Completed'; break;
        case 'IN_PROGRESS': baseLabel = 'In Progress'; showReason = false; break;
        case 'FAILED': baseLabel = 'Failed'; break;
        case 'FILE_TYPE_NOT_SUPPORTED': baseLabel = 'File Type Not Supported'; break;
        case 'NOT_STARTED': baseLabel = 'Not Started'; break;
        case 'QUEUED': baseLabel = 'Queued'; showReason = false; break;
        case 'AUTO_INDEX_OFF': baseLabel = 'Manual Indexing'; break;
        case 'EMPTY': baseLabel = 'Empty'; break;
        default: baseLabel = 'Queued'; showReason = false;
      }
      return appendReason(baseLabel, item.reason, showReason);
    }
    let baseLabel: string;
    let showReason = true;
    switch (item.status) {
      case 'indexed': baseLabel = 'Completed'; break;
      case 'processing': baseLabel = 'In Progress'; showReason = false; break;
      case 'pending': baseLabel = 'Pending'; showReason = false; break;
      case 'failed': baseLabel = 'Failed'; break;
      default: baseLabel = 'Queued'; showReason = false;
    }
    return appendReason(baseLabel, item.reason, showReason);
  };

  // Status indicator
  const getStatusIcon = () => {
    if (isFolder) return null;
    if (shouldHideIndexingStatusForHubRecord(item)) {
      return null;
    }

    // For KnowledgeHubNode, use indexingStatus
    if (isKnowledgeHubNode(item)) {
      // Missing status — no icon (avoid looking like "queued" via default branch)
      if (item.indexingStatus == null) {
        return null;
      }
      switch (item.indexingStatus) {
        case 'COMPLETED':
          return <MaterialIcon name={getIndexStatusIcon(item.indexingStatus)} size={16} color="var(--emerald-11)" />;
        case 'IN_PROGRESS':
          return <LapTimerIcon size={20} color="var(--amber-9)" />;
        case 'FAILED':
          return <MaterialIcon name={getIndexStatusIcon(item.indexingStatus)} size={16} color="var(--red-9)" />;
        case 'FILE_TYPE_NOT_SUPPORTED':
          return <MaterialIcon name={getIndexStatusIcon(item.indexingStatus)} size={16} color="var(--red-9)" />;
        case 'NOT_STARTED':
          return (
            <MaterialIcon name={getIndexStatusIcon('NOT_STARTED')} size={16} color="var(--slate-11)" />
          );
        case 'QUEUED':
          return (
            <MaterialIcon name={getIndexStatusIcon('QUEUED')} size={16} color="var(--blue-9)" />
          );
        case 'AUTO_INDEX_OFF':
          return (
            <MaterialIcon name={getIndexStatusIcon('AUTO_INDEX_OFF')} size={16} color="var(--olive-11)" />
          );
        case 'EMPTY':
          return (
            <MaterialIcon name={getIndexStatusIcon('EMPTY')} size={16} color="var(--slate-11)" />
          );
        default:
          return <MaterialIcon name="schedule" size={16} color="var(--blue-9)" />;
      }
    }

    // For KnowledgeBaseItem, use status
    switch (item.status) {
      case 'indexed':
        return <MaterialIcon name="check_circle" size={16} color="var(--emerald-11)" />;
      case 'processing':
        return <MaterialIcon name="sync" size={16} color="var(--amber-9)" />;
      case 'pending':
        return <MaterialIcon name="schedule" size={16} color="var(--blue-9)" />;
      case 'failed':
        return <MaterialIcon name="error" size={16} color="var(--red-9)" />;
      default:
        return <MaterialIcon name="schedule" size={16} color="var(--blue-9)" />;
    }
  };

  // Keyboard event handler
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isEditing) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <Flex
      ref={rowRef}
      align="center"
      tabIndex={0}
      role="row"
      aria-selected={isSelected}
      aria-label={item.name}
      style={{
        height: '60px',
        backgroundColor: isSelected
          ? 'var(--accent-3)'
          : isHovered
          ? 'var(--olive-2)'
          : 'var(--olive-1)',
        borderTop: isFocused ? '1px solid var(--accent-5)' : 'none',
        borderRight: isFocused ? '1px solid var(--accent-5)' : 'none',
        borderBottom: isFocused ? '1px solid var(--accent-5)' : '1px solid var(--olive-3)',
        borderLeft: isFocused ? '1px solid var(--accent-5)' : 'none',
        cursor: 'pointer',
        userSelect: 'none',
        outline: 'none',
        backdropFilter: 'blur(8px)',
        // transition: 'all 0.15s ease',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={() => setIsFocused(true)}
      onBlur={() => setIsFocused(false)}
      onKeyDown={handleKeyDown}
      onClick={onClick}
    >
      {/* Checkbox */}
      <Flex
        align="center"
        justify="center"
        style={{ width: '38px', padding: '0 8px', cursor: 'pointer' }}
        onClick={(e) => e.stopPropagation()}
      >
        <Checkbox
          size="1"
          checked={isSelected}
          onCheckedChange={() => onSelect()}
          style={{cursor: 'pointer'}}
        />
      </Flex>

      {/* File Name */}
      <Flex
        align="center"
        gap="2"
        style={{ flex: 1, padding: '0 var(--space-2)', minWidth: 0 }}
      >
        <KbNodeNameIcon
          isKnowledgeHub={isKnowledgeHubNode(item)}
          nodeType={isKnowledgeHubNode(item) ? item.nodeType : undefined}
          connector={isKnowledgeHubNode(item) ? item.connector : undefined}
          subType={isKnowledgeHubNode(item) ? item.subType : undefined}
          extension={isKnowledgeHubNode(item) ? item.extension : undefined}
          mimeType={isKnowledgeHubNode(item) ? item.mimeType ?? undefined : undefined}
          legacyType={!isKnowledgeHubNode(item) ? item.type : undefined}
          legacyFileType={!isKnowledgeHubNode(item) ? item.fileType : undefined}
          name={item.name}
          size={20}
        />
        {isEditing ? (
          <>
            {/* Hidden span to measure text width */}
            <span
              ref={measureRef}
              style={{
                position: 'absolute',
                visibility: 'hidden',
                whiteSpace: 'pre',
                font: 'inherit',
                fontSize: '14px',
                padding: 'var(--space-1) var(--space-2)',
              }}
            />
            <input
              ref={editInputRef}
              value={editValue}
              onChange={(e) => {
                setEditValue(e.target.value);
                const width = measureTextWidth(e.target.value);
                if (width !== undefined) {
                  setInputWidth(width + 24);
                }
              }}
              onBlur={handleRenameSave}
              onKeyDown={handleRenameKeyDown}
              onClick={(e) => e.stopPropagation()}
              onDoubleClick={(e) => e.stopPropagation()}
              style={{
                font: 'inherit',
                fontSize: '14px',
                color: 'var(--slate-12)',
                backgroundColor: 'var(--slate-1)',
                border: '1px solid var(--accent-8)',
                borderRadius: 'var(--radius-1)',
                padding: 'var(--space-1) var(--space-2)',
                outline: 'none',
                width: inputWidth ? `${inputWidth}px` : 'auto',
                minWidth: '60px',
                maxWidth: '100%',
              }}
            />
          </>
        ) : (
          <Text
            size="2"
            onClick={onRename ? (e: React.MouseEvent) => {
              e.stopPropagation();
              startEditing();
            } : undefined}
            style={{
              color: 'var(--slate-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              cursor: onRename ? 'text' : 'default',
            }}
          >
            {item.name}
          </Text>
        )}
        {isFolder && !item.hasChildren && (
          <Text
            size="1"
            style={{
              color: 'var(--slate-a11)',
              background: 'var(--surface-1)',
              border: '1px solid var(--slate-a7)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-1) var(--space-2)',
              whiteSpace: 'nowrap',
              flexShrink: 0,
            }}
          >
            Empty
          </Text>
        )}
      </Flex>

      {/* Status — tooltip only when an icon exists (avoid empty tooltip when status is null) */}
      <Flex align="center" justify="center" style={{ width: '60px', padding: '0 var(--space-2)' }}>
        {(() => {
          if (shouldHideIndexingStatusForHubRecord(item)) {
            return (
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                —
              </Text>
            );
          }
          const statusIcon = getStatusIcon();
          const statusLabel = getStatusLabel();
          if (!statusIcon) {
            return <Box style={{ display: 'inline-flex', minHeight: '20px' }} />;
          }
          return (
            <Tooltip content={statusLabel} side="top" delayDuration={200}>
              <Box style={{ display: 'inline-flex' }}>{statusIcon}</Box>
            </Tooltip>
          );
        })()}
      </Flex>

      {/* Source — All Records rows only; icon + source name on hover */}
      {showSourceColumn && isAllRecordDisplayRow(item) && (
        <Flex align="center" justify="center" gap="2" style={{ width: '70px', padding: '0 var(--space-2)' }}>
          <Tooltip content={item.sourceName} side="top" delayDuration={200}>
            <Box style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              {item.sourceType === 'collection' ? (
                <FolderIcon variant="default" size={16} color="var(--emerald-11)" />
              ) : (
                <ConnectorIcon type={item.sourceType} size={16} />
              )}
            </Box>
          </Tooltip>
        </Flex>
      )}

      {/* Size — hidden on mobile to keep the row readable */}
      {!isMobile && (
        <Flex align="center" style={{ width: '89px', padding: '0 var(--space-2)' }}>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {isKnowledgeHubNode(item)
              ? formatSize(item.sizeInBytes ?? undefined)
              : formatSize(item.size)
            }
          </Text>
        </Flex>
      )}

      {/* Created — hidden on mobile */}
      {!isMobile && (
        <Flex align="center" style={{ width: '147px', padding: '0 var(--space-2)' }}>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {isKnowledgeHubNode(item)
              ? formatDate(new Date(item.createdAt).toISOString())
              : formatDate(item.createdAt)
            }
          </Text>
        </Flex>
      )}

      {/* Updated — hidden on mobile */}
      {!isMobile && (
        <Flex align="center" style={{ width: '146px', padding: '0 var(--space-2)' }}>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {isKnowledgeHubNode(item)
              ? formatDate(new Date(item.updatedAt).toISOString())
              : formatDate(item.updatedAt)
            }
          </Text>
        </Flex>
      )}

      {/* Actions */}
      <Flex align="center" gap="1" style={{ width: '80px', padding: '0 var(--space-2)' }}>
        <ItemActionMenu
          open={isMenuOpen}
          onOpenChange={setIsMenuOpen}
          actions={[
            { icon: 'folder_open', label: 'Open', onClick: onOpen },
            !isFolder && onDownload && { icon: 'file_download', label: 'Download', onClick: () => onDownload(item) },
            onRename && { icon: 'edit', label: 'Rename', onClick: () => startEditing() },
            onReindex && !(isKnowledgeHubNode(item) && item.nodeType === 'app') && {
              icon: isKnowledgeHubNode(item) && item.indexingStatus === 'COMPLETED' ? 'redo' : 'refresh',
              label: isKnowledgeHubNode(item) && item.indexingStatus === 'COMPLETED' ? 'Force Reindex' : 'Reindex',
              onClick: () => onReindex(item),
            },
            !isFolder && onReplace && { icon: 'drive_folder_upload', label: 'Replace', onClick: () => onReplace(item) },
            onMove && { icon: 'drive_file_move', label: 'Move', onClick: () => onMove(item) },
            onDelete && !(isKnowledgeHubNode(item) && item.nodeType === 'app') && { icon: 'delete', label: 'Delete', onClick: () => onDelete(item), color: 'red' as const },
          ]}
        />
      </Flex>
    </Flex>
  );
}

interface KbListViewProps {
  items: TableItem[];
  selectedItems: Set<string>;
  allSelected: boolean;
  showSourceColumn?: boolean;
  sort: SortConfig | AllRecordsSortConfig;
  pagination?: {
    page: number;
    limit: number;
    totalItems: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
  onSelectAll: () => void;
  onSelectItem: (id: string) => void;
  onItemClick: (item: TableItem) => void;
  onSort: (config: SortConfig | AllRecordsSortConfig) => void;
  onPageChange?: (page: number) => void;
  onLimitChange?: (limit: number) => void;
  onPreview?: (item: TableItem) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
}

export function KbListView({
  items,
  selectedItems,
  allSelected,
  showSourceColumn,
  sort,
  pagination,
  onSelectAll,
  onSelectItem,
  onItemClick,
  onSort,
  onPageChange,
  onLimitChange,
  onPreview,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete,
  onDownload,
}: KbListViewProps) {
  const isMobile = useIsMobile();
  console.log('pagination data', pagination);

  return (
    <>
      {/* Table Header */}
      <Flex
        align="center"
        style={{
          height: 'var(--space-9)',
          borderBottom: '1px solid var(--olive-3)',
          backgroundColor: 'var(--olive-2)',
          backdropFilter: 'blur(8px)',
          flexShrink: 0,
        }}
      >
        {/* Checkbox */}
        <Flex
          align="center"
          justify="center"
          style={{ width: '38px', padding: '0 var(--space-2)', cursor: 'pointer' }}
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox
            size="1"
            checked={allSelected}
            onCheckedChange={onSelectAll}
            style={{cursor: 'pointer'}}
          />
        </Flex>

        {/* File Name */}
        <TableHeaderCell label="File Name" field="name" flex={1} sort={sort} onSort={onSort} />

        {/* Status */}
        <TableHeaderCell label="Status" width="60px" sort={sort} onSort={onSort} />

        {/* Source - Only shown in All Records mode */}
        {showSourceColumn && (
          <TableHeaderCell label="Source" width="70px" sort={sort} onSort={onSort} />
        )}

        {/* Size — hidden on mobile */}
        {!isMobile && (
          <TableHeaderCell label="Size" field="size" sortable width="89px" sort={sort} onSort={onSort} />
        )}

        {/* Created — hidden on mobile */}
        {!isMobile && (
          <TableHeaderCell label="Created" field="createdAt" sortable width="147px" sort={sort} onSort={onSort} />
        )}

        {/* Updated — hidden on mobile */}
        {!isMobile && (
          <TableHeaderCell label="Updated" field="updatedAt" sortable width="146px" sort={sort} onSort={onSort} />
        )}

        {/* Actions */}
        <Box style={{ width: '80px' }} />
      </Flex>

      {/* Table Body */}
      <Box
        className="no-scrollbar"
        style={{ flex: 1, overflowY: 'auto' }}
      >
        {items.map((item) => (
          <TableRow
            key={item.id}
            item={item}
            isSelected={selectedItems.has(item.id)}
            showSourceColumn={showSourceColumn}
            isMobile={isMobile}
            onSelect={() => onSelectItem(item.id)}
            onClick={() => onItemClick(item)}
            onOpen={() => runItemMenuOpenFromMenu(item, onItemClick, onPreview)}
            onRename={onRename}
            onReindex={onReindex}
            onReplace={onReplace}
            onMove={onMove}
            onDelete={onDelete}
            onDownload={onDownload}
          />
        ))}
      </Box>

      {/* Pagination */}
      {pagination && (
        <Flex
          justify="between"
          align="center"
          style={{
            padding: 'var(--space-2) var(--space-4)',
            borderTop: '1px solid var(--olive-3)',
            borderBottom: '1px solid var(--olive-3)',
            background: 'var(--olive-2)',
            backdropFilter: 'blur(8px)',
            flexShrink: 0,
          }}
        >
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            Showing {((pagination.page - 1) * pagination.limit) + 1}-{Math.min(pagination.page * pagination.limit, pagination.totalItems)} of {pagination.totalItems} Items
          </Text>
          <Flex gap="3" align="center">
            {/* Previous Button */}
            <Flex
              align="center"
              gap="1"
              style={{
                cursor: pagination.hasPrev ? 'pointer' : 'not-allowed',
                opacity: pagination.hasPrev ? 1 : 0.5,
                color: 'var(--slate-11)',
              }}
              onClick={() => pagination.hasPrev && onPageChange?.(pagination.page - 1)}
            >
              <MaterialIcon name="chevron_left" size={16} />
              <Text size="2">Previous</Text>
            </Flex>

            {/* Page Number Box */}
            <Box
              style={{
                padding: 'var(--space-1) var(--space-3)',
                backgroundColor: 'var(--slate-3)',
                borderRadius: 'var(--radius-2)',
                minWidth: '32px',
                textAlign: 'center',
              }}
            >
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {pagination.page}
              </Text>
            </Box>

            {/* Next Button */}
            <Flex
              align="center"
              gap="1"
              style={{
                cursor: pagination.hasNext ? 'pointer' : 'not-allowed',
                opacity: pagination.hasNext ? 1 : 0.5,
                color: 'var(--slate-11)',
              }}
              onClick={() => pagination.hasNext && onPageChange?.(pagination.page + 1)}
            >
              <Text size="2">Next</Text>
              <MaterialIcon name="chevron_right" size={16} />
            </Flex>

            {/* Separator */}
            <Box style={{ width: '1px', height: '20px', backgroundColor: 'var(--olive-3)' }} />

            {/* Rows per page selector */}
            <DropdownMenu.Root>
              <DropdownMenu.Trigger>
                <Flex
                  align="center"
                  gap="1"
                  style={{
                    cursor: 'pointer',
                    padding: 'var(--space-1) var(--space-2)',
                    backgroundColor: 'var(--slate-3)',
                    borderRadius: 'var(--radius-2)',
                  }}
                >
                  <Text size="2" style={{ color: 'var(--slate-12)' }}>
                    {pagination.limit}
                  </Text>
                  <MaterialIcon name="expand_less" size={14} color="var(--slate-11)" />
                </Flex>
              </DropdownMenu.Trigger>
              <DropdownMenu.Content align="end" sideOffset={4}>
                {[10, 25, 50, 100].map((limit) => (
                  <DropdownMenu.Item
                    key={limit}
                    onClick={() => onLimitChange?.(limit)}
                  >
                    {limit} per page
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.Content>
            </DropdownMenu.Root>
          </Flex>
        </Flex>
      )}
    </>
  );
}
