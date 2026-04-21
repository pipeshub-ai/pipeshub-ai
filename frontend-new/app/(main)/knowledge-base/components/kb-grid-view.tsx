'use client';

import { useState, useRef, useEffect } from 'react';
import { Flex, Box, Text, Checkbox, IconButton, DropdownMenu } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon, FolderIcon } from '@/app/components/ui';
import { LapTimerIcon } from '@/app/components/ui/lap-timer-icon';
import { formatSize } from '@/lib/utils/formatters';
import { CARD_ICONS } from './grid-card-icons';
import { runItemMenuOpenFromMenu } from '../utils/kb-table-item-actions';

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
  onSelect: () => void;
  onClick: () => void;
  onOpen: () => void;
  onPreview?: (item: TableItem) => void;
  onRename?: (item: TableItem, newName: string) => Promise<void>;
  onReindex?: (item: TableItem) => void;
  onReplace?: (item: TableItem) => void;
  onMove?: (item: TableItem) => void;
  onDelete?: (item: TableItem) => void;
  onDownload?: (item: TableItem) => void;
}

function GridCard({
  item,
  isSelected,
  onSelect,
  onClick,
  onOpen,
  onPreview: _onPreview,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete,
  onDownload,
}: GridCardProps) {
  const [isHovered, setIsHovered] = useState(false);
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

  // Status badge component (only shown for files)
  const getStatusBadge = () => {
    if (isFolder) return null;

    // For KnowledgeHubNode, use indexingStatus
    if (isKnowledgeHubNode(item)) {
      switch (item.indexingStatus) {
        case 'COMPLETED':
          return (
            <Flex
              align="center"
              justify="center"
              style={{
                backgroundColor: 'var(--accent-2)',
                border: '1px solid var(--accent-7)',
                borderRadius: 'var(--radius-1)',
                padding: '4px 6px',
              }}
            >
              <MaterialIcon name="check_circle" size={12} color="var(--accent-a11)" />
            </Flex>
          );
        case 'IN_PROGRESS':
          return (
            <Flex
              align="center"
              justify="center"
              style={{
                backgroundColor: 'var(--amber-2)',
                border: '1px solid var(--amber-7)',
                borderRadius: 'var(--radius-1)',
              }}
            >
              <LapTimerIcon size={16} color="var(--amber-9)" />
            </Flex>
          );
        case 'FAILED':
          return (
            <Flex
              align="center"
              justify="center"
              style={{
                backgroundColor: 'var(--red-2)',
                border: '1px solid var(--red-7)',
                borderRadius: 'var(--radius-1)',
                padding: '4px 6px',
              }}
            >
              <MaterialIcon name="error_outline" size={12} color="var(--red-9)" />
            </Flex>
          );
        default:
          return (
            <Flex
              align="center"
              justify="center"
              style={{
                backgroundColor: 'var(--blue-2)',
                border: '1px solid var(--blue-7)',
                borderRadius: 'var(--radius-1)',
                padding: '4px 6px',
              }}
            >
              <MaterialIcon name="schedule" size={12} color="var(--blue-9)" />
            </Flex>
          );
      }
    }

    // For KnowledgeBaseItem, use status
    switch (item.status) {
      case 'indexed':
        return (
          <Flex
            align="center"
            justify="center"
            style={{
              backgroundColor: 'var(--accent-2)',
              border: '1px solid var(--accent-7)',
              borderRadius: 'var(--radius-1)',
              padding: '4px 6px',
            }}
          >
            <MaterialIcon name="check_circle" size={12} color="var(--accent-9)" />
          </Flex>
        );
      case 'processing':
        return (
          <Flex
            align="center"
            justify="center"
            style={{
              backgroundColor: 'var(--amber-2)',
              border: '1px solid var(--amber-7)',
              borderRadius: 'var(--radius-1)',
              padding: '4px 6px',
            }}
          >
            <MaterialIcon name="sync" size={12} color="var(--amber-9)" />
          </Flex>
        );
      case 'pending':
        return (
          <Flex
            align="center"
            justify="center"
            style={{
              backgroundColor: 'var(--blue-2)',
              border: '1px solid var(--blue-7)',
              borderRadius: 'var(--radius-1)',
              padding: '4px 6px',
            }}
          >
            <MaterialIcon name="schedule" size={12} color="var(--blue-9)" />
          </Flex>
        );
      case 'failed':
        return (
          <Flex
            align="center"
            justify="center"
            style={{
              backgroundColor: 'var(--red-2)',
              border: '1px solid var(--red-7)',
              borderRadius: 'var(--radius-1)',
              padding: '4px 6px',
            }}
          >
            <MaterialIcon name="error_outline" size={12} color="var(--red-9)" />
          </Flex>
        );
      default:
        return null;
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
      ref={cardRef}
      gap="1"
      align="start"
      tabIndex={0}
      role="gridcell"
      aria-selected={isSelected}
      aria-label={item.name}
      style={{
        height: '156px',
        backgroundColor: isSelected
          ? 'var(--accent-3)'
          : isHovered
          ? 'var(--olive-2)'
          : 'var(--olive-1)',
        border: isFocused
          ? '1px solid var(--accent-5)'
          : '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        paddingLeft: '8px',
        paddingRight: '16px',
        paddingTop: '16px',
        paddingBottom: '16px',
        cursor: 'pointer',
        userSelect: 'none',
        outline: 'none',
        // transition: 'all 0.15s ease',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocus={() => setIsFocused(true)}
      onBlur={() => setIsFocused(false)}
      onKeyDown={handleKeyDown}
      onClick={onClick}
    >
      {/* Checkbox column - only visible on hover or when selected */}
      <Flex
        align="center"
        style={{
          paddingTop: '4px',
          paddingBottom: '4px',
          flexShrink: 0,
          visibility: isHovered || isSelected ? 'visible' : 'hidden',
        }}
      >
        <Checkbox
          size="1"
          checked={isSelected}
          onCheckedChange={() => onSelect()}
          onClick={(e) => e.stopPropagation()}
        />
      </Flex>

      {/* Main content column */}
      <Flex
        direction="column"
        gap="5"
        style={{
          flex: 1,
          minWidth: 0, // Allow text truncation
        }}
      >
        {/* Top section: icon, name, size */}
        <Flex direction="column" gap="3" style={{ flex: 1 }}>
          {/* Icon and action buttons row */}
          <Flex align="center" justify="between">
            {/* File/Folder icon */}
            {isFolder ? (
              <Box style={{ width: '24px', height: '24px', flexShrink: 0 }}>
                <FolderIcon variant="default" size={20} color="var(--emerald-11)" />
              </Box>
            ) : (
              <Box style={{ width: '24px', height: '24px', flexShrink: 0 }}>
                <FileIcon
                  extension={
                    isKnowledgeHubNode(item)
                      ? item.extension || item.mimeType?.split('/')[1]
                      : item.fileType
                  }
                  size={24}
                  fallbackIcon="description"
                />
              </Box>
            )}
            
            {/* Action buttons - always present but styled differently */}
            <Flex gap="1" style={{ width: '80px' }} justify="end">
              <DropdownMenu.Root open={isMenuOpen} onOpenChange={setIsMenuOpen}>
                <DropdownMenu.Trigger>
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="gray"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <MaterialIcon name={CARD_ICONS.MORE_MENU} size={16} />
                  </IconButton>
                </DropdownMenu.Trigger>
                <DropdownMenu.Content onClick={(e) => e.stopPropagation()}>
                  <DropdownMenu.Item onClick={(e) => { e.stopPropagation(); onOpen(); }}>
                    <MaterialIcon name="folder_open" size={16} />
                    Open
                  </DropdownMenu.Item>
                  {!isFolder && onDownload && (
                    <DropdownMenu.Item onClick={(e) => { e.stopPropagation(); onDownload(item); }}>
                      <MaterialIcon name="file_download" size={16} />
                      Download
                    </DropdownMenu.Item>
                  )}
                  {onRename && (
                    <DropdownMenu.Item onClick={() => startEditing()}>
                      <MaterialIcon name="edit" size={16} />
                      Rename
                    </DropdownMenu.Item>
                  )}
                  {onReindex && !(isKnowledgeHubNode(item) && item.nodeType === 'app') && (
                    <DropdownMenu.Item onClick={() => onReindex(item)}>
                      <MaterialIcon name={isKnowledgeHubNode(item) && item.indexingStatus === 'COMPLETED' ? 'redo' : 'refresh'} size={16} />
                      {isKnowledgeHubNode(item) && item.indexingStatus === 'COMPLETED' ? 'Force Reindex' : 'Reindex'}
                    </DropdownMenu.Item>
                  )}
                  {!isFolder && onReplace && (
                    <DropdownMenu.Item onClick={() => onReplace(item)}>
                      <MaterialIcon name="swap_horiz" size={16} />
                      Replace
                    </DropdownMenu.Item>
                  )}
                  {onMove && (
                    <DropdownMenu.Item onClick={() => onMove(item)}>
                      <MaterialIcon name="drive_file_move" size={16} />
                      Move
                    </DropdownMenu.Item>
                  )}
                  {onDelete && !(isKnowledgeHubNode(item) && item.nodeType === 'app') && (
                      <DropdownMenu.Item onClick={() => onDelete(item)} color="red">
                        <MaterialIcon name="delete" size={16} />
                        Delete
                      </DropdownMenu.Item>
                  )}
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            </Flex>
          </Flex>

          {/* Name and size */}
          <Flex direction="column" gap="1" style={{ width: '100%'}}>
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
                    padding: '2px 6px',
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
                    padding: '2px 6px',
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
                weight="medium"
                onClick={onRename ? (e: React.MouseEvent) => {
                  e.stopPropagation();
                  startEditing();
                } : undefined}
                style={{
                  color: 'var(--slate-12)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  lineHeight: 'var(--line-height-2)',
                  cursor: onRename ? 'text' : 'default',
                  padding: '2px 6px',
                  border: '1px solid transparent',
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
                padding: '4px 8px',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                maxWidth: 'fit-content',
                }}
              >
                Empty
              </Text>
            )}
            <Text
              size="2"
              style={{
                color: 'var(--slate-10)',
                lineHeight: 'var(--line-height-2)',
              }}
            >
              {isKnowledgeHubNode(item)
                ? formatSize(item.sizeInBytes ?? undefined)
                : formatSize(item.size)
              }
            </Text>
          </Flex>
        </Flex>

        {/* Bottom section: status badge or placeholder */}
        <Flex align="center" style={{ minHeight: '20px' }}>
          {getStatusBadge() || (
            <Text
              size="2"
              weight="medium"
              style={{
                color: 'var(--slate-9)',
                lineHeight: 'var(--line-height-2)',
              }}
            >
              -
            </Text>
          )}
        </Flex>
      </Flex>
    </Flex>
  );
}

interface KbGridViewProps {
  items: TableItem[];
  selectedItems: Set<string>;
  pagination?: {
    page: number;
    limit: number;
    totalItems: number;
    totalPages: number;
    hasNext: boolean;
    hasPrev: boolean;
  };
  onSelectItem: (id: string) => void;
  onItemClick: (item: TableItem) => void;
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

export function KbGridView({
  items,
  selectedItems,
  pagination,
  onSelectItem,
  onItemClick,
  onPageChange,
  onLimitChange,
  onPreview,
  onRename,
  onReindex,
  onReplace,
  onMove,
  onDelete,
  onDownload,
}: KbGridViewProps) {
  return (
    <Flex direction="column" style={{ flex: 1, minHeight: 0 }}>
      {/* Grid content area */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px',
        }}
      >
        {/* Grid container - 3 columns with gap */}
        <Box
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: '12px',
          }}
        >
          {items.map((item) => (
            <GridCard
              key={item.id}
              item={item}
              isSelected={selectedItems.has(item.id)}
              onSelect={() => onSelectItem(item.id)}
              onClick={() => onItemClick(item)}
              onOpen={() => runItemMenuOpenFromMenu(item, onItemClick, onPreview)}
              onPreview={onPreview}
              onRename={onRename}
              onReindex={onReindex}
              onReplace={onReplace}
              onMove={onMove}
              onDelete={onDelete}
              onDownload={onDownload}
            />
          ))}
        </Box>
      </Box>

      {/* Pagination */}
      {pagination && (
        <Flex
          justify="between"
          align="center"
          style={{
            padding: '8px 16px',
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
                padding: '4px 12px',
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
                    padding: '4px 8px',
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
    </Flex>
  );
}
