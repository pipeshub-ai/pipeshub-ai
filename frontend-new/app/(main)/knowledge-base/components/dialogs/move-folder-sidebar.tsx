'use client';

import React, { useState, useMemo } from 'react';
import { Flex, Box, Text, Button, IconButton, TextField, Dialog, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FolderIcon } from '@/app/components/ui';
import { renderTreeLines } from '@/app/(main)/knowledge-base/sidebar/section-element';
import {
  TREE_INDENT_PER_LEVEL,
  TREE_BASE_PADDING,
} from '@/app/components/sidebar';
import type { FolderTreeNode } from '../../types';

/** Collect all descendant IDs from a tree node */
function collectDescendantIds(node: FolderTreeNode): Set<string> {
  const ids = new Set<string>();
  for (const child of node.children) {
    ids.add(child.id);
    collectDescendantIds(child).forEach((id) => ids.add(id));
  }
  return ids;
}

interface FolderTreeItemProps {
  node: FolderTreeNode;
  depth: number;
  selectedFolderId: string | null;
  currentFolderId: string | null;
  disabledIds: Set<string>;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  expandedFolders: Record<string, boolean>;
}

function FolderTreeItem({
  node,
  depth,
  selectedFolderId,
  currentFolderId,
  disabledIds,
  onSelect,
  onToggle,
  expandedFolders,
}: FolderTreeItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const isExpanded = !!expandedFolders[node.id];
  const hasChildren = node.children.length > 0;
  const indent = depth * TREE_INDENT_PER_LEVEL;
  const isSelected = selectedFolderId === node.id;
  const isCurrent = currentFolderId === node.id;
  const isDisabled = disabledIds.has(node.id);

  const getIconColor = () => {
    if (isDisabled) return 'var(--slate-8)';
    if (isCurrent) return 'var(--accent-11)';
    return 'var(--slate-11)';
  };

  const getTextColor = () => {
    if (isDisabled) return 'var(--slate-8)';
    if (isCurrent) return 'var(--accent-11)';
    return 'var(--slate-11)';
  };

  return (
    <>
      {/* position: relative required for renderTreeLines absolute-positioned lines */}
      <Box style={{ position: 'relative', height: '32px' }}>
        {/* Render vertical tree connector lines; startDepth=1 to skip root-level line */}
        {renderTreeLines(depth, 1)}
        <Flex
          align="center"
          justify="between"
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          onClick={() => {
            if (!isCurrent && !isDisabled) onSelect(node.id);
          }}
          style={{
            width: '100%',
            height: '32px',
            paddingLeft: `${TREE_BASE_PADDING + indent}px`,
            paddingRight: 'var(--space-3)',
            cursor: isCurrent || isDisabled ? 'default' : 'pointer',
            borderRadius: 'var(--radius-2)',
            backgroundColor: isSelected ? 'var(--olive-3)' : isHovered && !isCurrent && !isDisabled ? 'var(--slate-3)' : 'transparent',
          }}
        >
          <Flex align="center" gap="2">
            {/* Expand/Collapse icon */}
            <Box
              style={{
                width: '16px',
                height: '16px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: hasChildren ? 'pointer' : 'default',
              }}
              onClick={(e) => {
                e.stopPropagation();
                if (hasChildren) onToggle(node.id);
              }}
            >
              {hasChildren && (
                <MaterialIcon
                  name={isExpanded ? 'expand_more' : 'chevron_right'}
                  size={16}
                  color={getIconColor()}
                />
              )}
            </Box>

            {/* Folder icon */}
            <FolderIcon
              variant="default"
              size={16}
              color={isDisabled ? 'var(--slate-8)' : 'var(--emerald-11)'}
            />

            {/* Folder name */}
            <Text
              size="2"
              style={{
                color: getTextColor(),
                fontWeight: isCurrent ? 500 : 400,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {node.name}
            </Text>
          </Flex>

          {/* Current label */}
          {isCurrent && (
            <Text
              size="1"
              style={{
                color: 'var(--accent-a11)',
                fontWeight: 400,
              }}
            >
              Current
            </Text>
          )}
        </Flex>
      </Box>

      {/* Children */}
      {isExpanded &&
        node.children.map((child) => (
          <FolderTreeItem
            key={child.id}
            node={child}
            depth={depth + 1}
            selectedFolderId={selectedFolderId}
            currentFolderId={currentFolderId}
            disabledIds={disabledIds}
            onSelect={onSelect}
            onToggle={onToggle}
            expandedFolders={expandedFolders}
          />
        ))}
    </>
  );
}

export interface MoveFolderSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The folder the item currently resides in (marked as "Current", not selectable) */
  currentFolderId: string | null;
  /** The collection tree — collection as root node with folder children */
  collectionTree: FolderTreeNode | null;
  /** ID of the item being moved — used to disable it and its descendants as destinations */
  itemToMoveId?: string;
  onMove: (newParentId: string) => void;
  isMoving?: boolean;
}

export function MoveFolderSidebar({
  open,
  onOpenChange,
  currentFolderId,
  collectionTree,
  itemToMoveId,
  onMove,
  isMoving = false,
}: MoveFolderSidebarProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  // Auto-expand root collection node by default so children are visible on open
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({ __collection_root__: true });

  // Collect IDs that should be disabled (the item being moved + its descendants)
  const disabledIds = useMemo(() => {
    if (!itemToMoveId || !collectionTree) return new Set<string>();

    const findNode = (node: FolderTreeNode): FolderTreeNode | null => {
      if (node.id === itemToMoveId) return node;
      for (const child of node.children) {
        const found = findNode(child);
        if (found) return found;
      }
      return null;
    };

    const itemNode = findNode(collectionTree);
    if (!itemNode) return new Set<string>();

    const ids = collectDescendantIds(itemNode);
    ids.add(itemToMoveId);
    return ids;
  }, [itemToMoveId, collectionTree]);

  // Toggle folder expansion
  const handleToggle = (folderId: string) => {
    setExpandedFolders((prev) => {
      const next = { ...prev };
      if (next[folderId]) {
        delete next[folderId];
      } else {
        next[folderId] = true;
      }
      return next;
    });
  };

  // Handle folder selection
  const handleSelect = (folderId: string) => {
    if (folderId !== currentFolderId && !disabledIds.has(folderId)) {
      setSelectedFolderId(folderId);
    }
  };

  // Handle collection root selection (move to root)
  const handleSelectRoot = () => {
    if (!collectionTree) return;
    // Selecting collection root — only allowed if currentFolderId is not null
    // (i.e., the item is not already at root)
    setSelectedFolderId('__collection_root__');
  };

  // Handle move action
  const handleMove = () => {
    if (selectedFolderId === '__collection_root__') {
      onMove('');
    } else if (selectedFolderId) {
      onMove(selectedFolderId);
    }
  };

  // Filter folders by search query
  const filterFolders = (folders: FolderTreeNode[], query: string): FolderTreeNode[] => {
    if (!query.trim()) return folders;

    const lowerQuery = query.toLowerCase();

    const filterNode = (node: FolderTreeNode): FolderTreeNode | null => {
      const matchesName = node.name.toLowerCase().includes(lowerQuery);
      const filteredChildren = node.children
        .map(filterNode)
        .filter((child): child is FolderTreeNode => child !== null);

      if (matchesName || filteredChildren.length > 0) {
        return {
          ...node,
          children: filteredChildren,
        };
      }
      return null;
    };

    return folders
      .map(filterNode)
      .filter((node): node is FolderTreeNode => node !== null);
  };

  const filteredChildren = useMemo(
    () => (collectionTree ? filterFolders(collectionTree.children, searchQuery) : []),
    [collectionTree, searchQuery]
  );

  // Check if collection root name matches search
  const rootMatchesSearch = useMemo(() => {
    if (!searchQuery.trim() || !collectionTree) return true;
    return collectionTree.name.toLowerCase().includes(searchQuery.toLowerCase());
  }, [collectionTree, searchQuery]);

  const showRoot = rootMatchesSearch || filteredChildren.length > 0;
  const isRootSelected = selectedFolderId === '__collection_root__';
  const isRootCurrent = currentFolderId === null;
  const isRootExpanded = !!expandedFolders['__collection_root__'];
  const [isRootHovered, setIsRootHovered] = useState(false);

  // Reset state when dialog closes
  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen) {
      setSearchQuery('');
      setSelectedFolderId(null);
      setExpandedFolders({ __collection_root__: true });
    }
    onOpenChange(isOpen);
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: '37.5rem',
          maxWidth: '100vw',
          maxHeight: '100vh',
          padding: 0,
          margin: 0,
          background: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          backdropFilter: 'blur(25px)',
          boxShadow: '0 20px 48px 0 rgba(0, 0, 0, 0.25)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>Move Folder Location</Dialog.Title>
        </VisuallyHidden>
        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '8px 8px 8px 16px',
            borderBottom: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <Flex align="center" gap="2">
            <MaterialIcon
              name="drive_file_move"
              size={24}
              color="var(--slate-11)"
            />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Move Folder Location
            </Text>
          </Flex>
          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => handleOpenChange(false)}
          >
            <MaterialIcon name="close" size={16} color="var(--slate-11)" />
          </IconButton>
        </Flex>

        {/* Search */}
        <Box style={{ padding: '16px 16px 0', background: 'var(--effects-translucent)', backdropFilter: 'blur(8px)' }}>
          <TextField.Root
            size="2"
            placeholder="Search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--slate-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Box>

        {/* Folder tree */}
        <Box
          className="no-scrollbar"
          style={{
            padding: 'var(--space-4)',
            flex: 1,
            overflowY: 'auto',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(25px)',
          }}
        >
          {collectionTree && showRoot ? (
            <Flex direction="column">
              {/* Collection root node — rendered as part of the tree with expand/collapse chevron */}
              <Box style={{ position: 'relative', height: '32px' }}>
                <Flex
                  align="center"
                  justify="between"
                  onMouseEnter={() => setIsRootHovered(true)}
                  onMouseLeave={() => setIsRootHovered(false)}
                  onClick={() => {
                    if (!isRootCurrent) handleSelectRoot();
                  }}
                  style={{
                    width: '100%',
                    height: '32px',
                    paddingLeft: `${TREE_BASE_PADDING}px`,
                    paddingRight: 'var(--space-3)',
                    cursor: isRootCurrent ? 'default' : 'pointer',
                    borderRadius: 'var(--radius-2)',
                    backgroundColor: isRootSelected ? 'var(--olive-3)' : isRootHovered && !isRootCurrent ? 'var(--slate-3)' : 'transparent',
                  }}
                >
                  <Flex align="center" gap="2">
                    {/* Expand/collapse chevron for root — toggles children visibility */}
                    <Box
                      style={{
                        width: '16px',
                        height: '16px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: filteredChildren.length > 0 ? 'pointer' : 'default',
                      }}
                      onClick={(e: React.MouseEvent) => {
                        e.stopPropagation();
                        if (filteredChildren.length > 0) handleToggle('__collection_root__');
                      }}
                    >
                      {filteredChildren.length > 0 && (
                        <MaterialIcon
                          name={isRootExpanded ? 'expand_more' : 'chevron_right'}
                          size={16}
                          color={isRootCurrent ? 'var(--accent-11)' : 'var(--slate-11)'}
                        />
                      )}
                    </Box>
                    <MaterialIcon
                      name="folder_special"
                      size={16}
                      color={isRootCurrent ? 'var(--accent-11)' : 'var(--emerald-11)'}
                    />
                    <Text
                      size="2"
                      style={{
                        color: isRootCurrent ? 'var(--accent-11)' : 'var(--slate-12)',
                        fontWeight: 500,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {collectionTree.name}
                    </Text>
                  </Flex>

                  {isRootCurrent && (
                    <Text
                      size="1"
                      style={{
                        color: 'var(--accent-11)',
                        fontWeight: 400,
                      }}
                    >
                      Current
                    </Text>
                  )}
                </Flex>
              </Box>

              {/* Folder children — only rendered when root is expanded */}
              {isRootExpanded && filteredChildren.map((folder) => (
                <FolderTreeItem
                  key={folder.id}
                  node={folder}
                  depth={1}
                  selectedFolderId={selectedFolderId}
                  currentFolderId={currentFolderId}
                  disabledIds={disabledIds}
                  onSelect={handleSelect}
                  onToggle={handleToggle}
                  expandedFolders={expandedFolders}
                />
              ))}
            </Flex>
          ) : (
              <Flex align="center" justify="center" style={{ padding: 'var(--space-8)' }}>
              <Text size="2" style={{ color: 'var(--slate-9)' }}>
                {searchQuery ? 'No folders found' : 'No collection selected'}
              </Text>
            </Flex>
          )}
        </Box>

        {/* Footer */}
        <Flex
          align="center"
          justify="end"
          gap="2"
          style={{
            padding: '8px 16px',
            borderTop: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <Button
            variant="solid"
            size="2"
            disabled={!selectedFolderId || isMoving}
            onClick={handleMove}
            style={{
              background: 'var(--emerald-9)',
              color: 'white',
            }}
          >
            <MaterialIcon
              name="drive_file_move"
              size={16}
              color="white"
            />
            {isMoving ? 'Moving...' : 'Move'}
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
