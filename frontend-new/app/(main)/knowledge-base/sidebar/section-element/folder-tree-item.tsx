'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Box, Text, Button, TextField, Tooltip, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KbNodeNameIcon } from '../../utils/kb-node-name-icon';
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
  const hasMenuItems = menuActions.some(Boolean);

  return (
    <>
      <Box
        style={{
          position: 'relative',
          width: '100%',
          minWidth: 0,
          paddingLeft: 'var(--space-2)',
          height: `${ELEMENT_HEIGHT}px`,
          boxSizing: 'border-box',
          flexShrink: 0,
        }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {renderTreeLines(node.depth, showRootLines ? 0 : 1)}
        <Flex
          align="center"
          gap="1"
          style={{
            width: '100%',
            minWidth: 0,
            minHeight: `${ELEMENT_HEIGHT}px`,
            paddingLeft: `${TREE_BASE_PADDING + indent}px`,
            paddingRight: 'var(--space-2)',
            boxSizing: 'border-box',
            borderRadius: 'var(--radius-1)',
            backgroundColor: isSelected ? 'var(--olive-3)' : isHovered ? HOVER_BACKGROUND : 'transparent',
          }}
        >
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
            flex: 1,
            minWidth: 0,
            maxWidth: '100%',
            display: 'flex',
            alignItems: 'center',
            boxSizing: 'border-box',
            justifyContent: 'flex-start',
            padding: 0,
            height: `${ELEMENT_HEIGHT}px`,
            borderRadius: 0,
            backgroundColor: 'transparent',
            boxShadow: 'none',
            cursor: 'pointer',
          }}
        >
          {hasChildren ? (
            <Box
              style={{
                width: '16px',
                height: '16px',
                flexShrink: 0,
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
            <Box style={{ width: '20px', flexShrink: 0 }} />
          )}

          <Box style={{ marginRight: '4px', flexShrink: 0, display: 'inline-flex', alignItems: 'center' }}>
            <KbNodeNameIcon
              isKnowledgeHub
              nodeType={enhancedNode.nodeType}
              connector={enhancedNode.connector}
              subType={enhancedNode.subType}
              extension={enhancedNode.extension}
              mimeType={enhancedNode.mimeType}
              name={node.name}
              isSelected={isSelected}
              size={16}
            />
          </Box>

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
            <Box style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
              <Tooltip
                content={node.name}
                delayDuration={200}
                open={isNameTruncated ? undefined : false}
              >
                <Text
                  ref={nameRef}
                  size="2"
                  style={{
                    display: 'block',
                    color: isSelected ? 'var(--accent-11)' : 'var(--slate-11)',
                    fontWeight: isSelected ? 500 : 400,
                    whiteSpace: 'nowrap',
                    textAlign: 'left',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: '100%',
                  }}
                >
                  {node.name}
                </Text>
              </Tooltip>
            </Box>
          )}
        </Button>
        {showMeatballMenu && hasMenuItems && (
          <Box
            style={{
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <ItemActionMenu
              actions={menuActions}
              open={isMenuOpen}
              onOpenChange={setIsMenuOpen}
            />
          </Box>
        )}
        </Flex>
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
