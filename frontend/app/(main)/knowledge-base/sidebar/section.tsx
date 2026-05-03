'use client';

import React, { useState } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { SECTION_PADDING_BOTTOM, SECTION_HEADER_PADDING, ELEMENT_HEIGHT } from '@/app/components/sidebar';
import { FolderTreeItem } from './section-element';
import { FolderIcon } from '@/app/components/ui';
import { isKbCollectionsHubApp, mapConnectorType } from '../utils/all-records-transformer';
import { useTranslation } from 'react-i18next';
import type {
  KnowledgeHubNode,
  EnhancedFolderTreeNode,
  NodeType,
} from '../types';
import { KB_SECTION_HEADER_MARGIN_BOTTOM } from '@/app/components/sidebar/constants';
import { SidebarListShimmerRows } from './sidebar-list-shimmer';
import { SidebarLoadMoreButton } from './sidebar-load-more-button';

/** Convert KnowledgeHubNode to a tree row for FolderTreeItem. */
export function convertToTreeNode(node: KnowledgeHubNode, depth: number = 0): EnhancedFolderTreeNode {
  return {
    id: node.id,
    name: node.name,
    depth,
    parentId: null,
    isExpanded: false,
    children: [],
    nodeType: node.nodeType,
    hasChildren: node.hasChildren,
    permission: node.permission,
    origin: node.origin,
    connector: node.connector,
    subType: node.subType,
    extension: node.extension,
    mimeType: node.mimeType,
  };
}

// ========================================
// AppSection
// ========================================

interface AppSectionProps {
  app: KnowledgeHubNode;
  childNodes: KnowledgeHubNode[];
  isLoading: boolean;
  onFolderSelect: (nodeType: string, nodeId: string) => void;
  onFolderExpand: (nodeId: string, nodeType: NodeType) => Promise<void>;
  onToggleFolderExpanded: (folderId: string) => void;
  expandedFolders: Record<string, boolean>;
  loadingNodeIds: Set<string>;
  currentFolderId?: string | null;
  // When provided (KB app in All Records mode), use the categorized tree
  // (EnhancedFolderTreeNode[]) directly so that sub-folder children populated
  // by handleNodeExpand / mergeChildrenIntoTree are visible in the tree.
  categorizedTree?: EnhancedFolderTreeNode[];
  /** Non-KB connector app: nested tree built from API + lazy merges (same UX as categorizedTree) */
  connectorTree?: EnhancedFolderTreeNode[];

  // Meatball menu actions
  onReindex?: (nodeId: string) => void;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;

  // Overflow limit
  maxVisible?: number;
  onMore?: () => void;

  /** Server pagination: more direct children exist for this app (same hub API as chat picker) */
  appChildListHasMore?: boolean;
  onLoadMoreAppChildren?: () => void;
  appChildLoadMoreDisabled?: boolean;
}

/**
 * AppSection — Renders each app as its own section in All Records mode.
 *
 * App header has NO expand chevron (always shows children).
 * Children use the same tree structure as Collections (expand/collapse, tree lines).
 */
export function AppSection({
  app,
  childNodes: children,
  isLoading,
  onFolderSelect,
  onFolderExpand,
  onToggleFolderExpanded,
  expandedFolders,
  loadingNodeIds,
  currentFolderId,
  categorizedTree,
  connectorTree,
  onReindex,
  onRename,
  onDelete,
  maxVisible,
  onMore,
  appChildListHasMore,
  onLoadMoreAppChildren,
  appChildLoadMoreDisabled,
}: AppSectionProps) {
  const { t } = useTranslation();
  const isKbApp = isKbCollectionsHubApp(app);
  const connectorType = mapConnectorType(app.connector || app.name);
  const hierarchicalTree = categorizedTree ?? connectorTree;
  const treeLen = hierarchicalTree?.length ?? 0;
  /**
   * “⋯ More” overflow: KB uses `categorizedTree` length; non-KB uses flat `children`
   * when there is no tree yet (`treeLen === 0`). When overflow shows, inline
   * “Load more” for server pagination is hidden (see `showChildLoadInline`).
   */
  const showOverflowMore =
    Boolean(maxVisible) &&
    ((treeLen > 0 && treeLen > maxVisible) ||
      (treeLen === 0 && children.length > maxVisible));
  const showChildLoadInline =
    !isLoading &&
    Boolean(appChildListHasMore && onLoadMoreAppChildren && !showOverflowMore);

  return (
    <Box style={{ marginBottom: `${SECTION_PADDING_BOTTOM}px` }}>
      {/* App Header */}
      <Flex
        align="center"
        gap="1"
        style={{ padding: SECTION_HEADER_PADDING, marginBottom: KB_SECTION_HEADER_MARGIN_BOTTOM }}
      >
        {isKbApp ? (
          <FolderIcon variant="default" size={16} color="var(--emerald-11)" style={{ flexShrink: 0 }} />
        ) : (
          <ConnectorIcon type={connectorType} size={16} color="var(--slate-11)" style={{ flexShrink: 0 }} />
        )}
        <Text
          size="2"
          weight="medium"
          style={{ color: 'var(--slate-11)', flex: 1 }}
        >
          {app.name}
        </Text>
      </Flex>

      {/* Children — same tree structure as Collections */}
      <Box
        className="no-scrollbar"
        style={{ overflowX: 'auto', marginTop: 'var(--space-1)', minWidth: 0 }}
      >
      <Flex direction="column" gap="0">
        {isLoading ? (
          <SidebarListShimmerRows count={3} />
        ) : hierarchicalTree && hierarchicalTree.length > 0 ? (
            <>
              {(maxVisible ? hierarchicalTree.slice(0, maxVisible) : hierarchicalTree).map((node) => (
                <FolderTreeItem
                  key={node.id}
                  node={node}
                  isSelected={currentFolderId === node.id}
                  currentFolderId={currentFolderId}
                  onSelect={(id) => onFolderSelect(node.nodeType, id)}
                  onToggle={(id) => {
                    onToggleFolderExpanded(id);
                    if (node.hasChildren) {
                      onFolderExpand(id, node.nodeType as NodeType);
                    }
                  }}
                  expandedFolders={expandedFolders}
                  loadingNodeIds={loadingNodeIds}
                  onNodeExpand={onFolderExpand}
                  onNodeSelect={onFolderSelect}
                  showRootLines={false}
                  onReindex={onReindex}
                  onRename={onRename}
                  onDelete={onDelete}
                />
              ))}
              {maxVisible && hierarchicalTree.length > maxVisible && (
                <MoreButton onClick={onMore} />
              )}
            </>
        ) : children.length > 0 ? (
          <>
            {(maxVisible ? children.slice(0, maxVisible) : children).map((child) => (
              <FolderTreeItem
                key={child.id}
                node={convertToTreeNode(child, 0)}
                isSelected={currentFolderId === child.id}
                currentFolderId={currentFolderId}
                onSelect={(id) => {
                  const childNode = children.find(c => c.id === id);
                  if (childNode) {
                    onFolderSelect(childNode.nodeType, id);
                  }
                }}
                onToggle={(id) => {
                  onToggleFolderExpanded(id);
                  const childNode = children.find(c => c.id === id);
                  if (childNode && childNode.hasChildren) {
                    onFolderExpand(id, childNode.nodeType);
                  }
                }}
                expandedFolders={expandedFolders}
                loadingNodeIds={loadingNodeIds}
                onNodeExpand={onFolderExpand}
                onNodeSelect={onFolderSelect}
                showRootLines={false}
                onReindex={onReindex}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
            {maxVisible && children.length > maxVisible && (
              <MoreButton onClick={onMore} />
            )}
          </>
        ) : (
          <Text size="1" style={{ color: 'var(--slate-9)', padding: 'var(--space-2) var(--space-6)' }}>
            No items
          </Text>
        )}
        {showChildLoadInline && onLoadMoreAppChildren ? (
          <SidebarLoadMoreButton
            onClick={onLoadMoreAppChildren}
            disabled={appChildLoadMoreDisabled}
            loading={appChildLoadMoreDisabled}
            flexStyle={{
              paddingLeft: 'var(--space-6)',
              paddingTop: 'var(--space-1)',
            }}
          />
        ) : null}
      </Flex>
      </Box>
    </Box>
  );
}

// ========================================
// MoreButton
// ========================================

function MoreButton({ onClick }: { onClick?: () => void }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      gap="2"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onClick?.(); }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        height: `${ELEMENT_HEIGHT}px`,
        padding: '0 12px',
        borderRadius: 'var(--radius-1)',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'transparent',
        cursor: 'pointer',
        userSelect: 'none',
      }}
    >
      <MaterialIcon name="more_horiz" size={16} color="var(--slate-11)" />
      <Text size="2" style={{ color: 'var(--slate-11)' }}>{t('sidebar.more')}</Text>
    </Flex>
  );
}
