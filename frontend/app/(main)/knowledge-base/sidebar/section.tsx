'use client';

import React, { useMemo, useState } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import {
  SECTION_HEADER_PADDING,
  ELEMENT_HEIGHT,
  HOVER_BACKGROUND,
} from '@/app/components/sidebar';
import { appSectionKey } from '../utils/fetch-app-direct-children';

export { appSectionKey };
import { FolderTreeItem } from './section-element';
import { FolderIcon } from '@/app/components/ui';
import { isKbCollectionsHubApp, mapConnectorType } from '../utils/all-records-transformer';
import { useTranslation } from 'react-i18next';
import type {
  KnowledgeHubNode,
  EnhancedFolderTreeNode,
  NodeType,
  SidebarReindexHandler,
} from '../types';
import { KB_SECTION_HEADER_MARGIN_BOTTOM } from '@/app/components/sidebar/constants';
import { SidebarLoadMoreButton } from './sidebar-load-more-button';

/** Shift the whole subtree one indent level under the app icon (not the app chevron). */
function indentAppSectionTreeRoots(nodes: EnhancedFolderTreeNode[]): EnhancedFolderTreeNode[] {
  return nodes.map((n) => ({
    ...n,
    depth: n.depth + 1,
    children: n.children?.length
      ? indentAppSectionTreeRoots(n.children as EnhancedFolderTreeNode[])
      : n.children,
  }));
}

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
  onReindex?: SidebarReindexHandler;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;

  // Overflow limit
  maxVisible?: number;
  onMore?: () => void;

  /** Server pagination: more direct children exist for this app (same hub API as chat picker) */
  appChildListHasMore?: boolean;
  onLoadMoreAppChildren?: () => void;
  appChildLoadMoreDisabled?: boolean;

  /** App section collapsed/expanded (All Records) */
  isExpanded?: boolean;
  onChevronClick?: () => void;
  onAppSelect?: () => void;
}

/**
 * AppSection — Renders each app as its own section in All Records mode.
 *
 * Chevron toggles visibility and loads children on first expand; title navigates to the app table.
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
  isExpanded = true,
  onChevronClick,
  onAppSelect,
}: AppSectionProps) {
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

  const showChevron = app.hasChildren === true;
  const [headerHovered, setHeaderHovered] = useState(false);
  const hasChildContent =
    (hierarchicalTree != null && hierarchicalTree.length > 0) || children.length > 0;

  const visibleIndentedTree = useMemo(() => {
    if (!hierarchicalTree?.length) return [];
    const sliced = maxVisible ? hierarchicalTree.slice(0, maxVisible) : hierarchicalTree;
    return indentAppSectionTreeRoots(sliced);
  }, [hierarchicalTree, maxVisible]);

  return (
    <Box style={{ marginBottom: isExpanded ? 'var(--space-2)' : 0 }}>
      {/* App Header — chevron expands/collapses; icon+title opens app in table */}
      <Flex
        align="center"
        gap="2"
        style={{
          height: `${ELEMENT_HEIGHT}px`,
          padding: SECTION_HEADER_PADDING,
          marginBottom: isExpanded ? KB_SECTION_HEADER_MARGIN_BOTTOM : 0,
          borderRadius: 'var(--radius-2)',
          backgroundColor: headerHovered ? HOVER_BACKGROUND : 'transparent',
          boxSizing: 'border-box',
        }}
        onMouseEnter={() => setHeaderHovered(true)}
        onMouseLeave={() => setHeaderHovered(false)}
      >
        {showChevron ? (
          <Box
            role="button"
            tabIndex={0}
            aria-expanded={isExpanded}
            onClick={(e) => {
              e.stopPropagation();
              onChevronClick?.();
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                e.stopPropagation();
                onChevronClick?.();
              }
            }}
            style={{
              width: '16px',
              height: '16px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0,
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
          <Box
            aria-hidden
            style={{
              width: '16px',
              height: '16px',
              flexShrink: 0,
            }}
          />
        )}
        <Flex
          align="center"
          gap="1"
          role="button"
          tabIndex={0}
          onClick={() => onAppSelect?.()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onAppSelect?.();
            }
          }}
          style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
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
      </Flex>

      {/* Children — same tree structure as Collections */}
      {isExpanded && (hasChildContent || !isLoading) ? (
      <Box className="no-scrollbar" style={{ overflowX: 'auto', minWidth: 0 }}>
      <Flex direction="column" gap="0">
        {visibleIndentedTree.length > 0 ? (
            <>
              {visibleIndentedTree.map((node) => (
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
                node={convertToTreeNode(child, 1)}
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
            }}
          />
        ) : null}
      </Flex>
      </Box>
      ) : null}
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
