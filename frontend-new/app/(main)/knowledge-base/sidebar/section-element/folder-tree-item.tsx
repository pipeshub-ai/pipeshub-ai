'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Box, Text, Button, TextField, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FolderIcon } from '@/app/components/ui';
import {
  ELEMENT_HEIGHT,
  TREE_INDENT_PER_LEVEL,
  TREE_BASE_PADDING,
  HOVER_BACKGROUND,
} from '@/app/components/sidebar';
import { useTranslation } from 'react-i18next';
import { renderTreeLines } from './tree-lines';
import { ItemActionMenu } from '../../components/item-action-menu';
import type { MenuAction } from '../../components/item-action-menu';
import { useKnowledgeBaseStore } from '../../store';
import type {
  FolderTreeNode,
  NodeType,
  EnhancedFolderTreeNode,
} from '../../types';

// ========================================
// Types
// ========================================

export interface FolderTreeItemProps {
  node: FolderTreeNode;
  isSelected: boolean;
  currentFolderId?: string | null;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  expandedFolders: Record<string, boolean>;
  loadingNodeIds?: Set<string>;
  onNodeExpand?: (nodeId: string, nodeType: NodeType) => Promise<void>;
  onNodeSelect?: (nodeType: string, nodeId: string) => void;
  sectionType?: 'shared' | 'private';
  onReindex?: (nodeId: string) => void;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;
  showRootLines?: boolean;
}

// ========================================
// Component
// ========================================

/**
 * Recursive folder tree item — handles expand/collapse, inline rename,
 * tree lines, context menu, and child rendering.
 */
export function FolderTreeItem({
  node,
  isSelected,
  currentFolderId,
  onSelect,
  onToggle,
  expandedFolders,
  loadingNodeIds,
  onNodeExpand,
  onNodeSelect,
  sectionType,
  onReindex,
  onRename,
  onDelete,
  showRootLines = true,
}: FolderTreeItemProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [isNameTruncated, setIsNameTruncated] = useState(false);
  const editInputRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLSpanElement>(null);

  const isExpanded = !!expandedFolders[node.id];
  const enhancedNode = node as EnhancedFolderTreeNode;
  const { expandFolderExclusive } = useKnowledgeBaseStore();
  const hasChildren = enhancedNode.hasChildren || node.children.length > 0;
  const isLoading = loadingNodeIds?.has(node.id);
  const indent = node.depth * TREE_INDENT_PER_LEVEL;

  const showMeatballMenu = (isHovered || isMenuOpen) && !isEditing;

  const canEdit = enhancedNode.permission?.canEdit !== false;
  const canDelete = enhancedNode.permission?.canDelete !== false;

  useEffect(() => {
    if (isEditing && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [isEditing]);

  // Detect whether the name is actually overflowing so we only show a tooltip
  // when the text is truncated (avoids redundant tooltips for short names).
  useEffect(() => {
    const el = nameRef.current;
    if (!el) return;
    const check = () => setIsNameTruncated(el.scrollWidth > el.clientWidth + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [node.name, showMeatballMenu]);

  // ---- Rename handlers ----

  const handleRenameStart = () => {
    setEditValue(node.name);
    setIsEditing(true);
    setIsMenuOpen(false);
  };

  const handleRenameSave = async () => {
    const trimmed = editValue.trim();
    if (!trimmed || trimmed === node.name) {
      setIsEditing(false);
      return;
    }
    try {
      await onRename?.(node.id, trimmed);
    } catch {
      // Error handled by parent via toast
    }
    setIsEditing(false);
  };

  const handleRenameCancel = () => {
    setIsEditing(false);
    setEditValue('');
  };

  // ---- Toggle handler ----

  const handleToggle = async () => {
    const willExpand = !isExpanded;

    if (
      willExpand &&
      enhancedNode.hasChildren &&
      node.children.length === 0 &&
      onNodeExpand
    ) {
      await onNodeExpand(node.id, enhancedNode.nodeType);
    }

    if (willExpand) {
      // Exclusive expand: collapse siblings + their descendants
      expandFolderExclusive(node.id);
    } else {
      // Collapsing: just toggle off
      onToggle(node.id);
    }
  };

  // ---- Build meatball menu actions ----

  const menuActions: (MenuAction | false)[] = [
    { icon: 'refresh', label: t('menu.reindex'), onClick: () => onReindex?.(node.id) },
    canEdit && !!onRename && { icon: 'edit', label: t('menu.rename'), onClick: handleRenameStart },
    canDelete && !!onDelete && {
      icon: 'delete',
      label: t('menu.delete'),
      onClick: () => onDelete!(node.id),
      color: 'red' as const,
    },
  ];

  return (
    <>
      <Box
        style={{ position: 'relative', width: '100%', paddingLeft: 'var(--space-3)', height: `${ELEMENT_HEIGHT}px`, boxSizing: 'border-box', flexShrink: 0, minWidth: 'fit-content' }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {renderTreeLines(node.depth, showRootLines ? 0 : 1)}
        <Button
          variant="ghost"
          size="2"
          color="gray"
          onClick={() => {
            onSelect(node.id);
            if (onNodeSelect && enhancedNode.nodeType) {
              onNodeSelect(enhancedNode.nodeType, node.id);
            }
          }}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            justifyContent: 'flex-start',
            paddingLeft: `${TREE_BASE_PADDING + indent}px`,
            paddingRight: showMeatballMenu ? '32px' : '8px',
            borderRadius: 'var(--radius-1)',
            backgroundColor: isSelected ? 'var(--olive-3)' : isHovered ? HOVER_BACKGROUND : 'transparent',
          }}
        >
          {hasChildren ? (
            <Box
              style={{
                width: '16px',
                height: '16px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                marginRight: 'var(--space-1)',
                cursor: 'pointer',
              }}
              onClick={(e) => {
                e.stopPropagation();
                handleToggle();
              }}
            >
              {isLoading ? (
                <MaterialIcon
                  name="refresh"
                  size={16}
                  color="var(--slate-11)"
                  style={{ animation: 'spin 1s linear infinite' }}
                />
              ) : (
                <MaterialIcon
                  name={isExpanded ? 'expand_more' : 'chevron_right'}
                  size={16}
                  color="var(--slate-11)"
                />
              )}
            </Box>
          ) : (
            <Box style={{ width: '20px' }} />
          )}

          {enhancedNode.nodeType === 'app' ? (
            <MaterialIcon
              name="extension"
              size={16}
              color={isSelected ? 'var(--accent-9)' : 'var(--slate-11)'}
              style={{ marginRight: '4px' }}
            />
          ) : (
            <FolderIcon
              variant="default"
              size={16}
              color="var(--emerald-11)"
              style={{ marginRight: '4px' }}
            />
          )}

          {isEditing ? (
            <Box
              style={{ flex: 1, minWidth: 0 }}
              onClick={(e) => e.stopPropagation()}
            >
              <TextField.Root
                ref={editInputRef}
                size="1"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={(e) => {
                  e.stopPropagation();
                  if (e.key === 'Enter') {
                    handleRenameSave();
                  } else if (e.key === 'Escape') {
                    handleRenameCancel();
                  }
                }}
                onBlur={handleRenameSave}
                style={{ width: '100%' }}
              />
            </Box>
          ) : (
            <Tooltip
              content={node.name}
              delayDuration={200}
              open={isNameTruncated ? undefined : false}
            >
              <Text
                ref={nameRef}
                size="2"
                style={{
                  color: isSelected ? 'var(--accent-11)' : 'var(--slate-11)',
                  fontWeight: isSelected ? 500 : 400,
                  whiteSpace: 'nowrap',
                  textAlign: 'left',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {node.name}
              </Text>
            </Tooltip>
          )}
        </Button>

        {/* Meatball menu */}
        {showMeatballMenu && (
          <Box
            style={{
              position: 'absolute',
              right: '10px',
              top: '50%',
              transform: 'translateY(-50%)',
            }}
          >
            <ItemActionMenu
              actions={menuActions}
              open={isMenuOpen}
              onOpenChange={setIsMenuOpen}
            />
          </Box>
        )}
      </Box>

      {isExpanded &&
        node.children.map((child) => (
          <FolderTreeItem
            key={child.id}
            node={child}
            isSelected={currentFolderId === child.id}
            currentFolderId={currentFolderId}
            onSelect={onSelect}
            onToggle={onToggle}
            expandedFolders={expandedFolders}
            loadingNodeIds={loadingNodeIds}
            onNodeExpand={onNodeExpand}
            onNodeSelect={onNodeSelect}
            sectionType={sectionType}
            onReindex={onReindex}
            onRename={onRename}
            onDelete={onDelete}
            showRootLines={showRootLines}
          />
        ))}
    </>
  );
}
