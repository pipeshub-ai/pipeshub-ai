'use client';

import React, { useState, useRef, useEffect, type ReactNode } from 'react';
import { Flex, Box, Text, Checkbox, Button, DropdownMenu, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { formatSize, formatDate } from '@/lib/utils/formatters';
import { useIsMobile, useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { ItemActionMenu, type ItemActionMenuProps } from './item-action-menu';

type MenuActionList = ItemActionMenuProps['actions'];
import { KbPaginationFooter } from './kb-pagination';
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
import { EmptyBadge } from './empty-badge';
import { TableSkeleton } from '@/app/components/data-display/table-skeleton';
import { ProgressiveBlur } from '@/app/components/ui/progressive-blur';
import { rowRevealDelay, rowRevealEndMs } from '../utils/row-reveal';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import { isRateLimitPause } from '@/lib/utils/rate-limit-pause';
import { LapTimerIcon } from '@/app/components/ui/lap-timer-icon';
import {
  canDeleteKbTableItem,
  needsCollectionContext,
  runItemMenuOpenFromMenu,
  shouldHideIndexingStatusForHubRecord,
  shouldShowDownloadForTableItem,
} from '../utils/kb-table-item-actions';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { DemoSourceBadge } from '@/app/(main)/workspace/connectors/demo-data/components';
import {
  getReindexMenuState,
  getReindexNodeForTableItem,
  mapReindexOptionsToMenuActions,
} from '../utils/reindex-label';

/** "SOME_STATUS" → "Some Status", for statuses without a dedicated label. */
function humanizeStatus(status: string): string {
  return status
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

// Union type for items that can be displayed
export type TableItem = KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem;

/** Extra columns an edition overlay can add after "Updated" (desktop only). */
export interface KbListExtraColumns {
  header: ReactNode;
  cell: (item: TableItem) => ReactNode;
}

// Type guard to check if item is KnowledgeHubNode
function isKnowledgeHubNode(item: TableItem): item is KnowledgeHubNode {
  return 'nodeType' in item && 'origin' in item;
}

/** Indexing status in words, with the reason when it explains something (failures, pauses). Empty when there is none to show. */
export function getKbItemStatusLabel(item: TableItem, t: TFunction, withReason = true): string {
  if (shouldHideIndexingStatusForHubRecord(item)) {
    return '';
  }
  const appendReason = (base: string, reason?: string | null, showReason = true): string =>
    withReason && showReason && reason?.trim() ? `${base} - ${reason.trim()}` : base;

  if (isKnowledgeHubNode(item)) {
    // No status from API — do not imply "Queued"
    if (item.indexingStatus == null) {
      return '';
    }
    let baseLabel: string;
    let showReason = true;
    switch (item.indexingStatus) {
      case 'COMPLETED': baseLabel = t('kb.indexingStatus.completed'); showReason = false; break;
      case 'IN_PROGRESS': baseLabel = t('kb.indexingStatus.inProgress'); showReason = false; break;
      case 'FAILED': baseLabel = t('kb.indexingStatus.failed'); break;
      case 'FILE_TYPE_NOT_SUPPORTED': baseLabel = t('kb.indexingStatus.fileTypeNotSupported'); break;
      case 'NOT_STARTED': baseLabel = t('kb.indexingStatus.notStarted'); break;
      case 'QUEUED': baseLabel = t('kb.indexingStatus.queued'); showReason = isRateLimitPause(item.indexingStatus, item.reason); break;
      case 'AUTO_INDEX_OFF': baseLabel = t('kb.indexingStatus.manualIndexing'); break;
      case 'EMPTY': baseLabel = t('kb.indexingStatus.empty'); break;
      case 'PAUSED': baseLabel = t('kb.indexingStatus.paused'); break;
      case 'ENABLE_MULTIMODAL_MODELS': baseLabel = t('kb.indexingStatus.needsMultimodalModel'); break;
      case 'SKIPPED': baseLabel = t('kb.indexingStatus.skipped'); break;
      // Show an unrecognised status as itself: labelling it "Queued" hid real states.
      // It has no translation, since the UI does not know it.
      default: baseLabel = humanizeStatus(item.indexingStatus);
    }
    return appendReason(baseLabel, item.reason, showReason);
  }
  let baseLabel: string;
  let showReason = true;
  switch (item.status) {
    case 'indexed': baseLabel = t('kb.indexingStatus.completed'); showReason = false; break;
    case 'processing': baseLabel = t('kb.indexingStatus.inProgress'); showReason = false; break;
    case 'pending': baseLabel = t('kb.indexingStatus.pending'); showReason = false; break;
    case 'failed': baseLabel = t('kb.indexingStatus.failed'); break;
    default: baseLabel = t('kb.indexingStatus.queued'); showReason = false;
  }
  return appendReason(baseLabel, item.reason, showReason);
}

/** Indexing status icon for a record; null for folders and records without a status. */
export function getKbItemStatusIcon(item: TableItem, isFolder: boolean): ReactNode {
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
      case 'PAUSED':
        return <MaterialIcon name="pause_circle" size={16} color="var(--slate-11)" />;
      case 'ENABLE_MULTIMODAL_MODELS':
        return <MaterialIcon name="image_not_supported" size={16} color="var(--amber-11)" />;
      case 'SKIPPED':
        return <MaterialIcon name="skip_next" size={16} color="var(--slate-11)" />;
      default:
        // Neutral, not the "Queued" clock: an unknown status is not a queued one.
        return <MaterialIcon name="help_outline" size={16} color="var(--slate-11)" />;
    }
  }

  // For KnowledgeBaseItem, use status
  switch (item.status) {
    case 'indexed':
      return <MaterialIcon name="check" size={16} color="var(--accent-9)" />;
    case 'processing':
      return <MaterialIcon name="sync" size={16} color="var(--amber-9)" />;
    case 'pending':
      return <MaterialIcon name="schedule" size={16} color="var(--blue-9)" />;
    case 'failed':
      return <MaterialIcon name="error" size={16} color="var(--red-9)" />;
    default:
      return <MaterialIcon name="schedule" size={16} color="var(--blue-9)" />;
  }
}


export interface KbItemMenuHandlers {
  onOpen: () => void;
  /** Starts the inline rename in the row or card that owns the menu. */
  onStartRename: () => void;
  onDownload?: (item: TableItem) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem, statusFilters?: string[]) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
}

/** The "…" menu of one item, shared by the list rows and the grid cards so both offer the same actions. */
export function getKbItemMenuActions(item: TableItem, t: TFunction, h: KbItemMenuHandlers): MenuActionList {
  const isHubNode = isKnowledgeHubNode(item);
  const isFolder = isHubNode
    ? ['kb', 'app', 'folder', 'recordGroup'].includes(item.nodeType)
    : item.type === 'folder';
  // Per-item permission (Knowledge Hub nodes only): a shared item can have a lower
  // role than the folder it is listed in.
  const canEditItem = isHubNode ? (item.permission?.canEdit ?? false) : true;
  const { options: reindexMenuOptions, showMenu: showReindexMenu } = getReindexMenuState(
    getReindexNodeForTableItem(item, isHubNode),
    !!h.onReindex,
  );
  return [
    { icon: 'folder_open', label: t('kb.rowActions.open'), onClick: h.onOpen },
    !isFolder && h.onDownload && shouldShowDownloadForTableItem(item) && { icon: 'file_download', label: t('action.download'), onClick: () => h.onDownload!(item) },
    h.onRename && canEditItem && { icon: 'edit', label: t('menu.rename'), onClick: h.onStartRename },
    ...(showReindexMenu
      ? mapReindexOptionsToMenuActions(reindexMenuOptions, t, (statusFilters) => h.onReindex!(item, statusFilters))
      : []),
    !isFolder && h.onReplace && canEditItem && { icon: 'drive_folder_upload', label: t('action.replace'), onClick: () => h.onReplace!(item) },
    // Move relocates an item within its collection — a collection itself has nothing to move into.
    h.onMove && canEditItem && !(isHubNode && item.nodeType === 'app') && { icon: 'drive_file_move', label: t('action.move'), onClick: () => h.onMove!(item) },
    // Includes "a collection only by its OWNER", the same rule the bulk bar applies.
    h.onDelete && canDeleteKbTableItem(item) && { icon: 'delete', label: t('action.delete'), onClick: () => h.onDelete!(item), color: 'red' as const },
  ];
}

/** All Records rows extended with sourceName/sourceType from getSourceDisplay */
export function isAllRecordDisplayRow(item: TableItem): item is AllRecordItem {
  return 'sourceName' in item && 'sourceType' in item;
}

// Room for the preview, go-to-parent and more buttons.
const ACTIONS_WIDTH = '104px';
/** Phones keep only the 40px menu button (Open in the menu previews), plus go-to-parent on search results. */
const actionsWidth = (isMobile: boolean, hasGoToParent: boolean): string =>
  !isMobile ? ACTIONS_WIDTH : hasGoToParent ? '88px' : '56px';
const ACTION_SLOT: React.CSSProperties = { width: 24, display: 'flex', justifyContent: 'center', flexShrink: 0 };
export const ROW_ACTION_BUTTON: React.CSSProperties = {
  cursor: 'pointer',
  width: 24,
  height: 24,
  minWidth: 24,
  padding: 0,
  borderRadius: 'var(--radius-1)',
};

interface TableHeaderCellProps {
  label: string;
  field?: SortField;
  sortable?: boolean;
  width?: string;
  flex?: number;
  sort?: SortConfig | AllRecordsSortConfig;
  onSort?: (config: SortConfig | AllRecordsSortConfig) => void;
}

export function TableHeaderCell({
  label,
  field,
  sortable,
  width,
  flex,
  sort,
  onSort,
}: TableHeaderCellProps) {
  const isActive = sortable && field && sort?.field === field;

  const handleClick = () => {
    if (sortable && field && sort && onSort) {
      const newOrder: SortOrder =
        sort?.field === field && sort?.order === 'asc' ? 'desc' : 'asc';
      onSort?.({ field, order: newOrder } as SortConfig | AllRecordsSortConfig);
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
          height: 36,
          padding: '0 var(--space-2)',
          cursor: 'pointer',
          backgroundColor: 'transparent',
        }}
      >
        <Text size="1" style={{ color: 'var(--neutral-9)', fontWeight: 400 }}>
          {label}
        </Text>
        <MaterialIcon
          name={
            isActive
              ? sort?.order === 'asc'
                ? 'arrow_drop_up'
                : 'arrow_drop_down'
              : 'arrow_drop_down'
          }
          size={16}
          color={'var(--neutral-9)'}
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
        height: 36,
        padding: '0 var(--space-2)',
      }}
    >
      <Text size="1" style={{ color: 'var(--neutral-9)', fontWeight: 400 }}>
        {label}
      </Text>
    </Flex>
  );
}

interface TableRowProps {
  item: TableItem;
  isSelected: boolean;
  showSourceColumn?: boolean;
  showCheckbox?: boolean;
  isMobile: boolean;
  /** Below desktop the Size and Created columns (and any extra column) are dropped to keep the name readable. */
  belowDesktop: boolean;
  extraColumns?: KbListExtraColumns | null;
  onSelect: () => void;
  onClick: () => void;
  onOpen: () => void;
  onPreview?: (item: TableItem) => void;
  onGoToParent?: (item: KnowledgeHubNode) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem, statusFilters?: string[]) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
  revealIndex?: number;
}

function TableRow({
  item,
  isSelected,
  showSourceColumn,
  showCheckbox = true,
  isMobile,
  belowDesktop,
  extraColumns = null,
  onSelect,
  onClick,
  onOpen,
  onPreview,
  onGoToParent,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete,
  onDownload,
  revealIndex = 0,
}: TableRowProps) {
  const { t } = useTranslation();
  const canHover = useCanHover();
  const [isHovered, setIsHovered] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [isNameTruncated, setIsNameTruncated] = useState(false);
  const editInputRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLSpanElement>(null);
  const [inputWidth, setInputWidth] = useState<number | undefined>(undefined);
  const measureRef = useRef<HTMLSpanElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);

  // Determine if item is a file (record) for extension preservation
  const isFile = isKnowledgeHubNode(item)
    ? item.nodeType === 'record'
    : item.type === 'file';

  // Per-item permission (Knowledge Hub nodes only) — a shared item can have a
  // lower role than the folder it's listed in, so gate actions on the item's
  // own permission rather than just whether the parent passed a callback.
  const canEditItem = isKnowledgeHubNode(item)
    ? (item.permission?.canEdit ?? false)
    : true;

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

  // Tooltip only when the name is actually cut off.
  useEffect(() => {
    if (isEditing) return;
    const el = nameRef.current;
    if (!el) return;
    const check = () => setIsNameTruncated(el.scrollWidth > el.clientWidth + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [item.name, isEditing]);

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
  const isHubNode = isKnowledgeHubNode(item);
  const isFolder = isHubNode
    ? ['kb', 'app', 'folder', 'recordGroup'].includes(item.nodeType)
    : item.type === 'folder';

  const canPreview =
    !!onPreview && isHubNode && item.nodeType === 'record' && item.previewRenderable !== false;
  // The address of a parent needs its type, which only `parent` carries; an App has none.
  const parentTarget =
    onGoToParent && isHubNode && item.nodeType !== 'app' && item.parent?.id ? item : null;

  // Phones show the status and source as text under the name instead of in their own columns.
  const mobileSubtitle = isMobile
    ? [
        getKbItemStatusLabel(item, t),
        showSourceColumn && isAllRecordDisplayRow(item) ? item.sourceName : '',
      ]
        .filter(Boolean)
        .join(' · ')
    : '';

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
      className="kb-row-in"
      align="center"
      tabIndex={0}
      role="row"
      aria-selected={isSelected}
      aria-label={item.name}
      style={{
        height: '60px',
        animationDelay: rowRevealDelay(revealIndex),
        backgroundColor: isSelected
          ? 'var(--accent-a3)'
          : isHovered
          ? 'var(--olive-2)'
          : 'var(--olive-1)',
        // An inset ring, not a border, so focusing a row does not shift its content.
        borderBottom: '1px solid var(--olive-3)',
        boxShadow: isFocused ? 'inset 0 0 0 1px var(--accent-5)' : undefined,
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
      {/* Checkbox column — kept as a spacer when hidden so the file name doesn't shift left */}
      <Flex
        align="center"
        justify="center"
        style={{ width: '36px', padding: '0 8px', cursor: showCheckbox ? 'pointer' : 'default' }}
        onClick={(e: React.MouseEvent) => {
          e.stopPropagation();
          if (showCheckbox) onSelect();
        }}
      >
        {showCheckbox && (
          <Checkbox
            size="1"
            checked={isSelected}
            onCheckedChange={() => onSelect()}
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
            style={{cursor: 'pointer'}}
          />
        )}
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
          isPlaceholder={isKnowledgeHubNode(item) ? item.isPlaceholder : undefined}
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
          <Flex direction="column" style={{ minWidth: 0, flex: '0 1 auto' }}>
          <Tooltip
            content={item.name}
            delayDuration={200}
            open={isNameTruncated ? undefined : false}
          >
            <Text
              ref={nameRef}
              size="2"
              onClick={onRename && canEditItem && canHover ? (e: React.MouseEvent) => {
                e.stopPropagation();
                startEditing();
              } : undefined}
              style={{
                // Shrinks to fit so the badges that follow sit right after the name.
                flex: '0 1 auto',
                minWidth: 0,
                color: 'var(--neutral-12)',
                fontWeight: 500,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                cursor: onRename && canEditItem && canHover ? 'text' : 'default',
              }}
            >
              {item.name}
            </Text>
          </Tooltip>
          {/* Phones drop the Status and Source columns; their text moves under the name. */}
          {isMobile && mobileSubtitle && (
            <Text size="1" style={{ color: 'var(--neutral-9)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {mobileSubtitle}
            </Text>
          )}
          </Flex>
        )}
        {!isEditing && isKnowledgeHubNode(item) && (
          <DemoSourceBadge connectorId={item.connectorId} />
        )}
        {isFolder && !item.hasChildren && <EmptyBadge />}
      </Flex>

      {/* Status — tooltip only when an icon exists (avoid empty tooltip when status is null) */}
      {!isMobile && (
      <Flex align="center" justify="center" style={{ width: '60px', padding: '0 var(--space-2)' }}>
        {(() => {
          if (shouldHideIndexingStatusForHubRecord(item)) {
            return (
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                —
              </Text>
            );
          }
          const statusIcon = getKbItemStatusIcon(item, isFolder);
          const statusLabel = getKbItemStatusLabel(item, t);
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
      )}

      {/* Source — All Records rows only; icon + source name on hover */}
      {!isMobile && showSourceColumn && isAllRecordDisplayRow(item) && (
        <Flex align="center" justify="start" gap="2" style={{ width: '60px', padding: '0 var(--space-2)' }}>
          <Tooltip content={item.sourceName} side="top" delayDuration={200}>
            <Box style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
              {item.sourceType === 'collection' ? (
                <FolderIcon variant="default" size={20} color="var(--accent-9)" />
              ) : (
                <ConnectorIcon type={item.sourceType} size={16} />
              )}
            </Box>
          </Tooltip>
        </Flex>
      )}

      {/* Size — hidden on mobile to keep the row readable */}
      {!belowDesktop && (
        <Flex align="center" style={{ width: '89px', padding: '0 var(--space-2)' }}>
          <Text size="2" style={{ color: 'var(--neutral-9)', fontWeight: 500 }}>
            {isKnowledgeHubNode(item)
              ? formatSize(item.sizeInBytes ?? undefined)
              : formatSize(item.size)
            }
          </Text>
        </Flex>
      )}

      {/* Created — hidden on mobile. Skip when timestamp is 0 (source has no real value). */}
      {!belowDesktop && (
        <Flex align="center" style={{ width: '147px', padding: '0 var(--space-2)' }}>
          <Text size="2" style={{ color: 'var(--neutral-9)', fontWeight: 500 }}>
            {isKnowledgeHubNode(item)
              ? (item.createdAt ? formatDate(item.createdAt) : '-')
              : (item.createdAt ? formatDate(item.createdAt) : '-')
            }
          </Text>
        </Flex>
      )}

      {/* Updated — hidden on mobile. Skip when timestamp is 0. */}
      {!isMobile && (
        <Flex align="center" style={{ width: '146px', padding: '0 var(--space-2)' }}>
          <Text size="2" style={{ color: 'var(--neutral-9)', fontWeight: 500 }}>
            {isKnowledgeHubNode(item)
              ? (item.updatedAt ? formatDate(item.updatedAt) : '-')
              : (item.updatedAt ? formatDate(item.updatedAt) : '-')
            }
          </Text>
        </Flex>
      )}

      {!belowDesktop && extraColumns ? extraColumns.cell(item) : null}

      {/* Actions */}
      {/* A slot per action the list offers, filled or not, so the icons line up from row to row. */}
      <Flex align="center" justify="center" gap="2" style={{ width: actionsWidth(isMobile, !!onGoToParent), padding: '0 var(--space-2)' }}>
        {onPreview && !isMobile && (
          <Box style={ACTION_SLOT}>
            {canPreview && (
              <Tooltip content={t('kb.rowActions.preview')} side="top" delayDuration={200}>
                <IconButton
                  variant="ghost"
                  size="1"
                  color="gray"
                  aria-label={t('kb.rowActions.preview')}
                  onClick={(e) => {
                    e.stopPropagation();
                    onPreview!(item);
                  }}
                  style={ROW_ACTION_BUTTON}
                >
                  <MaterialIcon name="visibility" size={16} color="var(--neutral-11)" />
                </IconButton>
              </Tooltip>
            )}
          </Box>
        )}
        {onGoToParent && (
          <Box style={ACTION_SLOT}>
            {parentTarget && (
              <Tooltip
                content={parentTarget.parent?.name ? t('kb.rowActions.goToNamed', { name: parentTarget.parent.name }) : t('kb.rowActions.goToParent')}
                side="top"
                delayDuration={200}
              >
                <IconButton
                  variant="ghost"
                  size="1"
                  color="gray"
                  aria-label={t('kb.rowActions.goToParent')}
                  onClick={(e) => {
                    e.stopPropagation();
                    onGoToParent!(parentTarget);
                  }}
                  style={ROW_ACTION_BUTTON}
                >
                  <MaterialIcon name="north_west" size={16} color="var(--neutral-11)" />
                </IconButton>
              </Tooltip>
            )}
          </Box>
        )}
        <Box style={isMobile ? { ...ACTION_SLOT, width: 40 } : ACTION_SLOT}>
          <ItemActionMenu
            triggerSize={isMobile ? '3' : '1'}
            open={isMenuOpen}
            onOpenChange={setIsMenuOpen}
            triggerLabel={t('kb.rowActions.moreFor', { name: item.name })}
            actions={getKbItemMenuActions(item, t, {
              onOpen,
              onStartRename: startEditing,
              onDownload,
              onRename,
              onReindex,
              onReplace,
              onMove,
              onDelete,
            })}
          />
        </Box>
      </Flex>
    </Flex>
  );
}

interface KbListViewProps {
  items: TableItem[];
  selectedItems: Set<string>;
  allSelected: boolean;
  showSourceColumn?: boolean;
  showCheckbox?: boolean;
  extraColumns?: KbListExtraColumns | null;
  sort: SortConfig | AllRecordsSortConfig;
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
  onSelectAll: () => void;
  onSelectItem: (id: string) => void;
  onItemClick: (item: TableItem) => void;
  onSort: (config: SortConfig | AllRecordsSortConfig) => void;
  onCursorChange?: (cursor: string | null, direction?: 'next' | 'prev') => void;
  onLimitChange?: (limit: number) => void;
  onPreview?: (item: TableItem) => void;
  /** Set while the rows are search or filter results: each row then offers its parent. */
  onGoToParent?: (item: KnowledgeHubNode) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem, statusFilters?: string[]) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
  /** Rows are on their way: hold the header and shimmer the body until they land. */
  loading?: boolean;
  /** False when no collection is open (a search at the Collections root): see `needsCollectionContext`. */
  hasCollectionContext?: boolean;
}

const SKELETON_ROWS = 18;
const SKELETON_FADE_MS = 180;

export function KbListView({
  items,
  selectedItems,
  allSelected,
  showSourceColumn,
  showCheckbox = true,
  extraColumns = null,
  sort,
  pagination,
  onSelectAll,
  onSelectItem,
  onItemClick,
  onSort,
  onCursorChange,
  onLimitChange,
  onPreview,
  onGoToParent,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete,
  onDownload,
  loading = false,
  hasCollectionContext = true,
}: KbListViewProps) {
  const allows = (item: TableItem, action: 'rename' | 'move' | 'delete') =>
    hasCollectionContext || !needsCollectionContext(item, action);
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const belowDesktop = useIsBelowDesktop();
  // After a load the skeleton stays under the rows while they land one by one,
  // so each placeholder is replaced in place instead of the body going blank.
  const [revealing, setRevealing] = useState(false);
  const [wasLoading, setWasLoading] = useState(loading);
  if (wasLoading !== loading) {
    setWasLoading(loading);
    setRevealing(wasLoading && !loading);
  }

  return (
    <>
      {/* Table Header */}
      <Flex
        align="center"
        style={{
          height: 36,
          borderBottom: '1px solid var(--olive-3)',
          backgroundColor: 'var(--olive-2)',
          backdropFilter: 'blur(8px)',
          flexShrink: 0,
        }}
      >
        {/* Checkbox column — kept as a spacer when hidden so the File Name column doesn't shift left */}
        <Flex
          align="center"
          justify="center"
          style={{ width: '36px', padding: '0 var(--space-2)', cursor: showCheckbox ? 'pointer' : 'default' }}
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
        >
          {showCheckbox && (
            <Checkbox
              size="1"
              checked={allSelected}
              onCheckedChange={onSelectAll}
              style={{cursor: 'pointer'}}
            />
          )}
        </Flex>

        {/* File Name */}
        <TableHeaderCell label={t('table.fileName')} field="name" flex={1} sort={sort} onSort={onSort} />

        {/* Status — on phones it moves under the name */}
        {!isMobile && <TableHeaderCell label={t('table.status')} width="60px" sort={sort} onSort={onSort} />}

        {/* Source - Only shown in All Records mode, under the name on phones */}
        {showSourceColumn && !isMobile && (
          <TableHeaderCell label={t('table.source')} width="60px" sort={sort} onSort={onSort} />
        )}

        {/* Size — hidden below desktop */}
        {!belowDesktop && (
          <TableHeaderCell label={t('table.size')} field="size" sortable width="89px" sort={sort} onSort={onSort} />
        )}

        {/* Created — hidden below desktop */}
        {!belowDesktop && (
          <TableHeaderCell label={t('table.created')} field="createdAt" sortable width="147px" sort={sort} onSort={onSort} />
        )}

        {/* Updated — hidden on mobile */}
        {!isMobile && (
          <TableHeaderCell label={t('table.updated')} field="updatedAt" sortable width="146px" sort={sort} onSort={onSort} />
        )}

        {!belowDesktop && extraColumns ? extraColumns.header : null}

        {/* Actions */}
        <Box style={{ width: actionsWidth(isMobile, !!onGoToParent) }} />
      </Flex>

      {/* Table Body */}
      <Box style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {(loading || revealing) && (
        <Box
          role={loading ? 'status' : undefined}
          aria-label={loading ? t('common.loading', { defaultValue: 'Loading' }) : undefined}
          onAnimationEnd={(e) => {
            if (e.target === e.currentTarget) setRevealing(false);
          }}
          style={{
            position: 'absolute',
            inset: 0,
            overflow: 'hidden',
            pointerEvents: 'none',
            animation: loading
              ? undefined
              : `kbSkeletonOut ${SKELETON_FADE_MS}ms ease-out ${rowRevealEndMs(items.length)}ms forwards`,
          }}
        >
          <TableSkeleton
            rows={SKELETON_ROWS}
            rowHeight={60}
            checkboxWidth={36}
            actionsWidth={parseInt(actionsWidth(isMobile, !!onGoToParent), 10)}
            columns={[
              { barWidth: 0.62 },
              ...(!isMobile ? [{ width: '60px', barWidth: 0.45 }] : []),
              ...(showSourceColumn && !isMobile ? [{ width: '60px', barWidth: 0.45 }] : []),
              ...(!belowDesktop ? [{ width: '89px', barWidth: 0.55 }, { width: '147px', barWidth: 0.7 }] : []),
              ...(!isMobile ? [{ width: '146px', barWidth: 0.7 }] : []),
            ]}
          />
        </Box>
      )}
      <Box
        className="no-scrollbar"
        style={{ position: 'relative', flex: 1, minHeight: 0, overflowY: 'auto' }}
      >
        {items.map((item, index) => (
          <TableRow
            key={item.id}
            revealIndex={index}
            item={item}
            isSelected={selectedItems.has(item.id)}
            showSourceColumn={showSourceColumn}
            showCheckbox={showCheckbox}
            isMobile={isMobile}
            belowDesktop={belowDesktop}
            extraColumns={extraColumns}
            onSelect={() => onSelectItem(item.id)}
            onClick={() => onItemClick(item)}
            onOpen={() => runItemMenuOpenFromMenu(item, onItemClick, onPreview)}
            onPreview={onPreview}
            onGoToParent={onGoToParent}
            onRename={allows(item, 'rename') ? onRename : undefined}
            onReindex={onReindex}
            onReplace={onReplace}
            onMove={allows(item, 'move') ? onMove : undefined}
            onDelete={allows(item, 'delete') ? onDelete : undefined}
            onDownload={onDownload}
          />
        ))}
        <Box style={{ height: 96 }} />
      </Box>
      {/* Rows dissolve under the floating composer dock instead of meeting it at a hard edge. */}
      <ProgressiveBlur height={104} />
      </Box>

      {/* Pagination */}
      {pagination && (
        <KbPaginationFooter pagination={pagination} onCursorChange={onCursorChange} onLimitChange={onLimitChange} />
      )}
    </>
  );
}
