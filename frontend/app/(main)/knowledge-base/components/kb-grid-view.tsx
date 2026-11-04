'use client';

import { useState, useRef, useEffect } from 'react';
import { Flex, Box, Text, Checkbox, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KbNodeNameIcon } from '../utils/kb-node-name-icon';
import { EmptyBadge } from './empty-badge';
import { KbPaginationFooter } from './kb-pagination';
import { ItemActionMenu } from './item-action-menu';
import {
  getKbItemMenuActions,
  getKbItemStatusIcon,
  getKbItemStatusLabel,
  isAllRecordDisplayRow,
  ROW_ACTION_BUTTON,
} from './kb-list-view';
import { rowRevealDelay } from '../utils/row-reveal';
import { formatDate, formatSize } from '@/lib/utils/formatters';
import {
  needsCollectionContext,
  runItemMenuOpenFromMenu,
  shouldHideIndexingStatusForHubRecord,
} from '../utils/kb-table-item-actions';
import { useTranslation } from 'react-i18next';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { DemoSourceBadge } from '@/app/(main)/workspace/connectors/demo-data/components';
import type {
  KnowledgeBaseItem,
  KnowledgeHubNode,
  AllRecordItem
} from '../types';

// Union type for items that can be displayed
type TableItem = KnowledgeBaseItem | KnowledgeHubNode | AllRecordItem;

// Type guard to check if item is KnowledgeHubNode
function isKnowledgeHubNode(item: TableItem): item is KnowledgeHubNode {
  return 'nodeType' in item && 'origin' in item;
}

interface GridCardProps {
  item: TableItem;
  isSelected: boolean;
  showCheckbox?: boolean;
  /** Something is selected: every card shows its checkbox, as in a selection mode. */
  selectionActive?: boolean;
  isMobile: boolean;
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

function GridCard({
  item,
  isSelected,
  showCheckbox = true,
  selectionActive = false,
  isMobile,
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
}: GridCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const canHover = useCanHover();
  const [isFocused, setIsFocused] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);
  const [inputWidth, setInputWidth] = useState<number | undefined>(undefined);
  const measureRef = useRef<HTMLSpanElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);

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

  const isHubNode = isKnowledgeHubNode(item);
  const isFolder = isHubNode
    ? ['kb', 'app', 'folder', 'recordGroup'].includes(item.nodeType)
    : item.type === 'folder';
  const canPreview =
    !!onPreview && !isMobile && isHubNode && item.nodeType === 'record' && item.previewRenderable !== false;
  // The address of a parent needs its type, which only `parent` carries; an App has none.
  const parentTarget =
    onGoToParent && isHubNode && item.nodeType !== 'app' && item.parent?.id ? item : null;

  const hideStatus = shouldHideIndexingStatusForHubRecord(item);
  const statusIcon = getKbItemStatusIcon(item, isFolder);
  // The card shows the short label; the reason (often a long sentence) is in the tooltip.
  const statusLabel = statusIcon ? getKbItemStatusLabel(item, t) : '';
  const statusShort = statusIcon ? getKbItemStatusLabel(item, t, false) : '';
  const size = isHubNode ? item.sizeInBytes : item.size;
  // One line of facts: where it comes from (All Records), how big, when it last changed.
  const meta = [
    isAllRecordDisplayRow(item) ? item.sourceName : '',
    !isFolder && size ? formatSize(size) : '',
    item.updatedAt ? formatDate(item.updatedAt) : '',
  ]
    .filter(Boolean)
    .join(' · ');

  const showCheckboxNow = showCheckbox && (isHovered || isFocused || isSelected || selectionActive || !canHover);

  // Keyboard event handler
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isEditing || e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <Flex
      ref={cardRef}
      className="kb-row-in"
      direction="column"
      gap="2"
      tabIndex={0}
      role="gridcell"
      aria-selected={isSelected}
      aria-label={item.name}
      style={{
        minWidth: 0,
        animationDelay: rowRevealDelay(revealIndex),
        backgroundColor: isSelected ? 'var(--accent-a3)' : isHovered ? 'var(--olive-2)' : 'var(--olive-1)',
        border: `1px solid ${isSelected ? 'var(--accent-8)' : isHovered ? 'var(--olive-5)' : 'var(--olive-3)'}`,
        // A ring, not a thicker border, so focusing a card does not shift its content.
        boxShadow: isFocused ? '0 0 0 2px var(--accent-8)' : undefined,
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-3)',
        cursor: 'pointer',
        userSelect: 'none',
        outline: 'none',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={(e) => setIsFocused(e.target === e.currentTarget)}
      onBlur={() => setIsFocused(false)}
      onKeyDown={handleKeyDown}
      onClick={onClick}
    >
      {/* Top row: selection, type, then the same actions a list row offers */}
      <Flex align="center" gap="2" style={{ height: isMobile ? 40 : 24 }}>
        {/* Kept as a spacer when hidden so the icon doesn't shift */}
        <Flex
          align="center"
          className="touch-hit"
          style={{ flexShrink: 0, visibility: showCheckboxNow ? 'visible' : 'hidden' }}
          onClick={(e: React.MouseEvent) => {
            e.stopPropagation();
            if (showCheckbox) onSelect();
          }}
        >
          {showCheckbox && (
            <Checkbox
              size="1"
              checked={isSelected}
              aria-label={t('kb.rowActions.select', { name: item.name })}
              onCheckedChange={() => onSelect()}
              onClick={(e: React.MouseEvent) => e.stopPropagation()}
              style={{ cursor: 'pointer' }}
            />
          )}
        </Flex>
        <Box style={{ width: 20, height: 20, flexShrink: 0, display: 'flex', alignItems: 'center' }}>
          <KbNodeNameIcon
            isKnowledgeHub={isHubNode}
            nodeType={isHubNode ? item.nodeType : undefined}
            connector={isHubNode ? item.connector : undefined}
            subType={isHubNode ? item.subType : undefined}
            extension={isHubNode ? item.extension : undefined}
            mimeType={isHubNode ? item.mimeType ?? undefined : undefined}
            legacyType={!isHubNode ? item.type : undefined}
            legacyFileType={!isHubNode ? item.fileType : undefined}
            name={item.name}
            size={20}
            isPlaceholder={isHubNode ? item.isPlaceholder : undefined}
          />
        </Box>
        {isHubNode && <DemoSourceBadge connectorId={item.connectorId} />}
        <Box style={{ flex: 1 }} />
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
              style={{ ...ROW_ACTION_BUTTON, margin: 0 }}
            >
              <MaterialIcon name="visibility" size={16} color="var(--neutral-11)" />
            </IconButton>
          </Tooltip>
        )}
        {parentTarget && (
          <Tooltip
            content={parentTarget.parent?.name ? t('kb.rowActions.goToNamed', { name: parentTarget.parent.name }) : t('kb.rowActions.goToParent')}
            side="top"
            delayDuration={200}
          >
            <IconButton
              variant="ghost"
              size={isMobile ? '3' : '1'}
              color="gray"
              aria-label={t('kb.rowActions.goToParent')}
              onClick={(e) => {
                e.stopPropagation();
                onGoToParent!(parentTarget);
              }}
              style={isMobile ? { margin: 0 } : { ...ROW_ACTION_BUTTON, margin: 0 }}
            >
              <MaterialIcon name="north_west" size={16} color="var(--neutral-11)" />
            </IconButton>
          </Tooltip>
        )}
        <ItemActionMenu
          triggerSize={isMobile ? '3' : '1'}
          triggerLabel={t('kb.rowActions.moreFor', { name: item.name })}
          open={isMenuOpen}
          onOpenChange={setIsMenuOpen}
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
      </Flex>

      {/* Name: two lines are always reserved, so every card in the grid has the same height */}
      <Box style={{ minHeight: 'calc(2 * var(--line-height-2))' }}>
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
            as="p"
            size="2"
            weight="medium"
            title={item.name}
            onClick={onRename && canEditItem && canHover ? (e: React.MouseEvent) => {
              e.stopPropagation();
              startEditing();
            } : undefined}
            style={{
              color: 'var(--slate-12)',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              wordBreak: 'break-word',
              lineHeight: 'var(--line-height-2)',
              cursor: onRename && canEditItem && canHover ? 'text' : 'default',
            }}
          >
            {item.name}
          </Text>
        )}
      </Box>

      {/* Indexing status in words (an icon alone did not say what it meant); folders show Empty instead */}
      <Flex align="center" gap="1" style={{ minHeight: 20, minWidth: 0 }}>
        {isFolder ? (
          !item.hasChildren && <EmptyBadge />
        ) : hideStatus ? (
          <Text size="1" style={{ color: 'var(--slate-9)' }}>—</Text>
        ) : statusIcon ? (
          <Tooltip content={statusLabel} side="top" delayDuration={200}>
            <Flex align="center" gap="1" style={{ minWidth: 0 }}>
              <Box style={{ display: 'inline-flex', flexShrink: 0 }}>{statusIcon}</Box>
              <Text size="1" truncate style={{ color: 'var(--neutral-11)' }}>
                {statusShort}
              </Text>
            </Flex>
          </Tooltip>
        ) : null}
      </Flex>

      <Text size="1" truncate title={meta} style={{ color: 'var(--neutral-9)', minHeight: 16 }}>
        {meta}
      </Text>
    </Flex>
  );
}

interface KbGridViewProps {
  items: TableItem[];
  selectedItems: Set<string>;
  /** With `onSelectAll`, a "Select all" checkbox sits above the cards, as the list has in its header. */
  allSelected?: boolean;
  onSelectAll?: () => void;
  showCheckbox?: boolean;
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
  onSelectItem: (id: string) => void;
  onItemClick: (item: TableItem) => void;
  onCursorChange?: (cursor: string | null, direction?: 'next' | 'prev') => void;
  onLimitChange?: (limit: number) => void;
  onPreview?: (item: TableItem) => void;
  /** Set while the cards are search or filter results: each card then offers its parent. */
  onGoToParent?: (item: KnowledgeHubNode) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem, statusFilters?: string[]) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
  /** False when no collection is open (a search at the Collections root): see `needsCollectionContext`. */
  hasCollectionContext?: boolean;
}

export function KbGridView({
  items,
  selectedItems,
  allSelected = false,
  onSelectAll,
  showCheckbox = true,
  pagination,
  onSelectItem,
  onItemClick,
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
  hasCollectionContext = true,
}: KbGridViewProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const allows = (item: TableItem, action: 'rename' | 'move' | 'delete') =>
    hasCollectionContext || !needsCollectionContext(item, action);
  return (
    <Flex direction="column" style={{ flex: 1, minHeight: 0 }}>
      {/* Grid content area */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 'var(--space-4)',
        }}
      >
        {showCheckbox && onSelectAll && (
          <Text as="label" size="1" style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-3)', color: 'var(--neutral-11)', cursor: 'pointer' }}>
            <Checkbox size="1" checked={allSelected} onCheckedChange={onSelectAll} style={{ cursor: 'pointer' }} />
            {t('table.selectAll')}
          </Text>
        )}
        {/* As many columns as fit; narrower on phones so two cards share a row */}
        <Box
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(auto-fill, minmax(${isMobile ? 150 : 200}px, 1fr))`,
            gap: 'var(--space-3)',
          }}
        >
          {items.map((item, index) => (
            <GridCard
              key={item.id}
              revealIndex={index}
              item={item}
              isSelected={selectedItems.has(item.id)}
              showCheckbox={showCheckbox}
              selectionActive={selectedItems.size > 0}
              isMobile={isMobile}
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
        </Box>
        <Box style={{ height: 96 }} />
      </Box>

      {/* Pagination */}
      {pagination && (
        <KbPaginationFooter pagination={pagination} onCursorChange={onCursorChange} onLimitChange={onLimitChange} />
      )}
    </Flex>
  );
}
