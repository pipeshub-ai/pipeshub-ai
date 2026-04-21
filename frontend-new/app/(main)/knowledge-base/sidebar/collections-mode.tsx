'use client';

import React, { useState } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SECTION_PADDING_BOTTOM, SECTION_HEADER_PADDING } from '@/app/components/sidebar';
import { FolderTreeItem } from './section-element';
import { useTranslation } from 'react-i18next';
import type {
  FolderTreeNode,
  NodeType,
  EnhancedFolderTreeNode,
} from '../types';
import { KB_SECTION_HEADER_MARGIN_BOTTOM } from '@/app/components/sidebar/constants';
import { LottieLoader } from '@/app/components/ui/lottie-loader';

// ========================================
// Types
// ========================================

interface CollectionsModeProps {
  sharedTree: (FolderTreeNode | EnhancedFolderTreeNode)[];
  privateTree: (FolderTreeNode | EnhancedFolderTreeNode)[];
  currentFolderId: string | null;
  selectedKbId?: string | null;
  onSelectKb?: (id: string) => void;
  onAddPrivate?: () => void;
  expandedFolders: Record<string, boolean>;
  onToggle: (id: string) => void;
  loadingNodeIds: Set<string>;
  isLoading?: boolean;
  onNodeExpand?: (nodeId: string, nodeType: NodeType) => Promise<void>;
  onNodeSelect?: (nodeType: string, nodeId: string) => void;
  onReindex?: (nodeId: string) => void;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;
}

// ========================================
// Add Button Component with Hover State
// ========================================

interface AddButtonProps {
  onClick: () => void;
}

function AddButton({ onClick }: AddButtonProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Box
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '20px',
        height: '20px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--slate-a3)' : 'transparent',
        transition: 'background-color 0.15s ease',
      }}
    >
      <MaterialIcon
        name="add"
        size={16}
        color={isHovered ? 'var(--slate-12)' : 'var(--slate-11)'}
      />
    </Box>
  );
}

// ========================================
// Internal: reusable collection tree section
// ========================================

interface CollectionTreeSectionProps {
  title: string;
  nodes: (FolderTreeNode | EnhancedFolderTreeNode)[];
  emptyMessage: string;
  sectionType: 'shared' | 'private';
  actionButton?: React.ReactNode;
  // tree props
  currentFolderId: string | null;
  selectedKbId?: string | null;
  onSelectKb?: (id: string) => void;
  expandedFolders: Record<string, boolean>;
  onToggle: (id: string) => void;
  loadingNodeIds: Set<string>;
  onNodeExpand?: (nodeId: string, nodeType: NodeType) => Promise<void>;
  onNodeSelect?: (nodeType: string, nodeId: string) => void;
  onReindex?: (nodeId: string) => void;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;
}

/**
 * A single collection tree section (Shared or Private).
 * Consolidates the duplicated header + tree + empty-state pattern.
 */
function CollectionTreeSection({
  title,
  nodes,
  emptyMessage,
  sectionType,
  actionButton,
  currentFolderId,
  selectedKbId,
  onSelectKb,
  expandedFolders,
  onToggle,
  loadingNodeIds,
  onNodeExpand,
  onNodeSelect,
  onReindex,
  onRename,
  onDelete,
}: CollectionTreeSectionProps) {

  return (
    <Box style={{ marginBottom: `${SECTION_PADDING_BOTTOM}px` }}>
      {/* Section header */}
      <Flex align="center" justify="between" style={{ padding: SECTION_HEADER_PADDING, marginBottom: KB_SECTION_HEADER_MARGIN_BOTTOM }}>
        <Text
          size="1"
          style={{
            color: 'var(--slate-11)',
            letterSpacing: '0.04px',
          }}
        >
          {title}
        </Text>
        {actionButton}
      </Flex>

      {/* Tree items or empty state */}
      {nodes.length > 0 ? (
        <Box className="no-scrollbar" style={{ overflow: 'hidden' }}>
          <Flex direction="column" gap="0">
            {nodes.map((node) => (
              <FolderTreeItem
                key={node.id}
                node={node}
                isSelected={currentFolderId === node.id || selectedKbId === node.id}
                currentFolderId={currentFolderId}
                onSelect={(id) => onSelectKb?.(id)}
                onToggle={onToggle}
                expandedFolders={expandedFolders}
                loadingNodeIds={loadingNodeIds}
                onNodeExpand={onNodeExpand}
                onNodeSelect={onNodeSelect}
                sectionType={sectionType}
                onReindex={onReindex}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </Flex>
        </Box>
      ) : (
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--slate-11)',
            display: 'block',
            padding: '0 8px',
            lineHeight: '16px',
          }}
        >
          {emptyMessage}
        </Text>
      )}
    </Box>
  );
}

// ========================================
// Collections Mode
// ========================================

/**
 * Collections mode sidebar content — renders back header,
 * then Shared and Private collection tree sections.
 */
export function CollectionsMode({
  sharedTree,
  privateTree,
  currentFolderId,
  selectedKbId,
  onSelectKb,
  onAddPrivate,
  expandedFolders,
  onToggle,
  loadingNodeIds,
  isLoading = false,
  onNodeExpand,
  onNodeSelect,
  onReindex,
  onRename,
  onDelete,
}: CollectionsModeProps) {
  const { t } = useTranslation();

  // Filter out app nodes (only show folders/collections)
  const filteredSharedTree = sharedTree.filter(
    (node) => !('nodeType' in node) || node.nodeType !== 'app'
  );
  const filteredPrivateTree = privateTree.filter(
    (node) => !('nodeType' in node) || node.nodeType !== 'app'
  );

  // Show a full-sidebar spinner only on the initial fetch (before any tree
  // data exists). Once trees are populated, subsequent loads (e.g. auto-expand)
  // should not blank out the sidebar.
  const hasAnyData = filteredSharedTree.length > 0 || filteredPrivateTree.length > 0;
  if (isLoading && !hasAnyData) {
    return (
      <Flex align="center" justify="center" style={{ padding: '24px 0' }}>
        <LottieLoader variant="loader" size={24} />
      </Flex>
    );
  }

  // Shared tree props passed to both sections
  const treeProps = {
    currentFolderId,
    selectedKbId,
    onSelectKb,
    expandedFolders,
    onToggle,
    loadingNodeIds,
    onNodeExpand,
    onNodeSelect,
    onReindex,
    onRename,
    onDelete,
  };

  return (
    <>
      {/* Shared section */}
      <CollectionTreeSection
        title={t('nav.shared')}
        nodes={filteredSharedTree}
        emptyMessage={t('sidebar.sharedWithYou')}
        sectionType="shared"
        {...treeProps}
      />

      {/* Private section */}
      <CollectionTreeSection
        title={t('nav.private')}
        nodes={filteredPrivateTree}
        emptyMessage={t('sidebar.noPrivateYet')}
        sectionType="private"
        actionButton={onAddPrivate ? <AddButton onClick={onAddPrivate} /> : undefined}
        {...treeProps}
      />
    </>
  );
}
