'use client';

import React from 'react';
import Link from 'next/link';
import { Flex, Box, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SECTION_PADDING_BOTTOM, SECTION_CONTENT_MARGIN_TOP, EMPTY_STATE_PADDING_X, EMPTY_STATE_PADDING_Y, FEATURED_ITEM_MARGIN_BOTTOM, ELEMENT_BORDER, SIDEBAR_COLLECTION_LIMIT } from '@/app/components/sidebar';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useTranslation } from 'react-i18next';
import { KBSectionHeader } from './section-header';
import { AppSection, appSectionKey } from './section';
import { SidebarLoadMoreButton } from './sidebar-load-more-button';
import { isKbCollectionsHubApp } from '../utils/all-records-transformer';
import { findNodeInCategorized } from '../utils/find-node';
import { ConnectorItemComponent, MoreConnectorItem } from './section-element';
import type {
  Connector,
  ConnectorItem as ConnectorItemType,
  MoreConnectorLink,
  KnowledgeHubNode,
  NodeType,
  EnhancedFolderTreeNode,
  SidebarReindexHandler,
} from '../types';

// ========================================
// Types
// ========================================

interface AllRecordsModeProps {
  // Selection state
  isAllSelected: boolean;
  isConnectorItemSelected: (connectorType: string, itemId: string) => boolean;
  onSelectAll: () => void;
  onSelectCollection: (collection: { id: string; name: string }) => void;
  onSelectConnectorItem: (connectorType: string, item: ConnectorItemType) => void;

  // Data
  appNodes: KnowledgeHubNode[];
  appChildrenCache: Map<string, KnowledgeHubNode[]>;
  connectorAppTrees: Map<string, EnhancedFolderTreeNode[]>;
  loadingAppIds: Set<string>;
  /** Per-app hub child pagination (direct children under each app node) */
  appChildrenPagination?: Map<string, { hasNext: boolean; nextPage: number }>;
  onLoadMoreAppChildPage?: (appId: string) => void;
  connectors: Connector[];
  moreConnectors: MoreConnectorLink[];

  // Section expansion
  expandedSections: Set<string>;
  onToggleSection: (sectionId: string) => void;

  // Folder tree props (for AppSection)
  expandedFolders: Record<string, boolean>;
  onToggleFolderExpanded: (folderId: string) => void;
  loadingNodeIds: Set<string>;
  currentFolderId: string | null;
  onFolderSelect?: (nodeType: string, nodeId: string) => void;
  onNodeExpand?: (nodeId: string, nodeType: NodeType) => Promise<void>;

  // Optional categorized trees for the KB app section (shared + private).
  // When provided, AppSection renders sub-folder hierarchy correctly.
  kbSharedTree?: EnhancedFolderTreeNode[];
  kbPrivateTree?: EnhancedFolderTreeNode[];

  // Navigation
  onNavigateToConnectors: () => void;
  onNavigateToConnector: (connectorTypeParam: string) => void;
  connectorsHref: string;
  buildConnectorHref: (connectorTypeParam: string) => string;

  // Meatball menu actions
  onReindex?: SidebarReindexHandler;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;

  // Overflow "More" panel
  onOpenMoreFolders?: (appId: string, appName: string, connector: string) => void;
  onAppSelect?: (appId: string) => void;
  onExpandApp?: (appId: string) => void;

  /** Server-backed pagination: more root app nodes (connectors) available */
  onLoadMoreRootApps?: () => void;
  rootAppListHasNext?: boolean;
  isLoadingRootAppListMore?: boolean;
}

// ========================================
// All Records Mode
// ========================================

/**
 * All Records mode sidebar content — renders back header,
 * "All" button, Collections, App sections, Connector sections,
 * and More Connectors.
 */
export function AllRecordsMode({
  isAllSelected,
  isConnectorItemSelected,
  onSelectAll,
  onSelectCollection,
  onSelectConnectorItem,
  appNodes,
  appChildrenCache,
  connectorAppTrees,
  loadingAppIds,
  appChildrenPagination,
  onLoadMoreAppChildPage,
  connectors,
  moreConnectors,
  expandedSections,
  onToggleSection,
  expandedFolders,
  onToggleFolderExpanded,
  loadingNodeIds,
  currentFolderId,
  onFolderSelect,
  onNodeExpand,
  kbSharedTree,
  kbPrivateTree,
  onNavigateToConnectors,
  onNavigateToConnector,
  connectorsHref,
  buildConnectorHref,
  onReindex,
  onRename,
  onDelete,
  onOpenMoreFolders,
  onAppSelect,
  onExpandApp,
  onLoadMoreRootApps,
  rootAppListHasNext,
  isLoadingRootAppListMore,
}: AllRecordsModeProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  return (
    <>
      {/* "All" item */}
      <Button
        variant="soft"
        size="2"
        color={isAllSelected ? undefined : 'gray'}
        onClick={onSelectAll}
        style={{
          width: '100%',
          justifyContent: 'flex-start',
          marginBottom: `${FEATURED_ITEM_MARGIN_BOTTOM}px`,
          border: ELEMENT_BORDER,
          backgroundColor: isAllSelected ? 'var(--olive-3)' : 'transparent',
        }}
      >
        <Text
          size="2"
          style={{
            color: 'var(--slate-11)',
            fontWeight: 500,
          }}
        >
          {t('nav.all')}
        </Text>
      </Button>

      {/* App sections — KB app (Collections) appears first due to sorting in page.tsx */}
      {appNodes.map((app) => {
        const isKbApp = isKbCollectionsHubApp(app);
        const appChildren = appChildrenCache.get(app.id) || [];
        const childPageMeta = appChildrenPagination?.get(app.id);
        const appChildListHasMore = childPageMeta?.hasNext === true;
        // connectorAppTrees is populated for BOTH KB and connector apps (see
        // fetch-app-direct-children.ts) as soon as this app's chevron is
        // expanded here in All Records mode.
        const connectorTree = connectorAppTrees.get(app.id);
        // For KB apps, prefer the categorized tree when it's already populated
        // (e.g. the user previously browsed this KB in Collections mode, which
        // can merge in deeper/nested data) — find its own node within the
        // categorized tree and use its children, NOT the whole forest, which
        // would include the app's own root node and render it as a duplicate
        // child of itself. Falls back to connectorTree via `??` below.
        const categorizedTree = isKbApp
          ? findNodeInCategorized(
              { shared: kbSharedTree || [], private: kbPrivateTree || [] },
              app.id
            ).node?.children as EnhancedFolderTreeNode[] | undefined
          : undefined;
        const sectionKey = appSectionKey(app.id);
        const isAppExpanded = expandedSections.has(sectionKey);
        return (
          <AppSection
            key={app.id}
            app={app}
            childNodes={appChildren}
            connectorTree={connectorTree}
            isExpanded={isAppExpanded}
            onChevronClick={() => {
              const willExpand = !expandedSections.has(sectionKey);
              onToggleSection(sectionKey);
              if (willExpand) {
                void onExpandApp?.(app.id);
              }
            }}
            onAppSelect={() => onAppSelect?.(app.id)}
            isLoading={loadingAppIds.has(app.id)}
            onFolderSelect={(nodeType, nodeId) => {
              // For KB root-level collections, also update the selection state
              if (isKbApp && (nodeType === 'kb' || nodeType === 'app')) {
                const namedNode =
                  categorizedTree?.find((n) => n.id === nodeId) ||
                  appChildren.find((c) => c.id === nodeId);
                if (namedNode) {
                  onSelectCollection({ id: namedNode.id, name: namedNode.name });
                }
              }
              onFolderSelect?.(nodeType, nodeId);
            }}
            onFolderExpand={async (nodeId, nodeType) => {
              await onNodeExpand?.(nodeId, nodeType);
            }}
            onToggleFolderExpanded={onToggleFolderExpanded}
            expandedFolders={expandedFolders}
            loadingNodeIds={loadingNodeIds}
            currentFolderId={currentFolderId}
            categorizedTree={categorizedTree}
            onReindex={onReindex}
            onRename={isKbApp ? onRename : undefined}
            onDelete={isKbApp ? onDelete : undefined}
            maxVisible={SIDEBAR_COLLECTION_LIMIT}
            onMore={() => onOpenMoreFolders?.(app.id, app.name, app.connector || app.name)}
            appChildListHasMore={appChildListHasMore}
            onLoadMoreAppChildren={
              appChildListHasMore && onLoadMoreAppChildPage
                ? () => onLoadMoreAppChildPage(app.id)
                : undefined
            }
            appChildLoadMoreDisabled={loadingAppIds.has(app.id)}
          />
        );
      })}

      {rootAppListHasNext && onLoadMoreRootApps ? (
        <SidebarLoadMoreButton
          onClick={onLoadMoreRootApps}
          disabled={isLoadingRootAppListMore}
          loading={isLoadingRootAppListMore}
          idleLabel={t('agentBuilder.loadMoreConnectors', { defaultValue: 'Load more connectors' })}
          flexStyle={{
            marginBottom: `${SECTION_PADDING_BOTTOM}px`,
            paddingLeft: 'var(--space-2)',
            paddingTop: 'var(--space-1)',
          }}
        />
      ) : null}

      {/* Legacy connector sections */}
      {connectors.map((connector) => (
        <Box key={connector.id} style={{ marginBottom: `${SECTION_PADDING_BOTTOM}px` }}>
          <KBSectionHeader
            title={connector.name}
            connectorType={connector.type}
            isExpanded={expandedSections.has(connector.type)}
            onToggle={() => onToggleSection(connector.type)}
          />
          {expandedSections.has(connector.type) && (
            <Flex direction="column" gap="0" style={{ marginTop: `${SECTION_CONTENT_MARGIN_TOP}px` }}>
              {connector.items.length > 0 ? (
                connector.items.map((item) => (
                  <ConnectorItemComponent
                    key={item.id}
                    item={item}
                    connectorType={connector.type}
                    isSelected={isConnectorItemSelected(connector.type, item.id)}
                    onSelect={() => onSelectConnectorItem(connector.type, item)}
                  />
                ))
              ) : (
                <Text size="1" style={{ color: 'var(--slate-9)', padding: `${EMPTY_STATE_PADDING_Y}px ${EMPTY_STATE_PADDING_X}px` }}>
                  {t('message.noItems')}
                </Text>
              )}
            </Flex>
          )}
        </Box>
      ))}

      {/* More Connectors section — hidden on mobile to keep the drawer compact */}
      {!isMobile && moreConnectors.length > 0 && (
        <Box style={{ marginTop: `${SECTION_PADDING_BOTTOM}px` }}>
          <Text
            size="2"
            style={{
              color: 'var(--slate-11)',
              letterSpacing: '0.04px',
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              padding: '4px 4px',
              marginBottom: 'var(--space-2)',
              fontWeight: 400,
              fontStyle: 'normal',
            }}
          >
            <MaterialIcon name="hub" size={16} color="var(--accent-11)" />
            {t('nav.moreConnectors')}
          </Text>
          <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-1)' }}>
            {moreConnectors.map((connector) => (
              <MoreConnectorItem key={connector.id} connector={connector} href={buildConnectorHref(connector.connectorTypeParam)} onNavigate={onNavigateToConnector} />
            ))}
            <Button
              asChild
              variant="ghost"
              size="2"
              color="gray"
            >
              <Link
                href={connectorsHref}
                onClick={(e: React.MouseEvent) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey) return;
                  e.preventDefault();
                  onNavigateToConnectors();
                }}
                style={{
                  width: '100%',
                  justifyContent: 'space-between',
                  paddingLeft: 'var(--space-3)',
                  display: 'flex',
                  alignItems: 'center',
                  textDecoration: 'none',
                  color: 'inherit',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <MaterialIcon name="hub" size={16} color="var(--accent-11)" />
                  <Text size="2" style={{ color: 'var(--slate-11)', fontWeight: 400, fontStyle: 'normal' }}>
                    {t('sidebar.seeMoreConnectors')}
                  </Text>
                </div>
                <Flex
                  align="center"
                  justify="center"
                  style={{ width: '24px', height: '24px', backgroundColor: 'var(--gray-a3)', borderRadius: 'var(--radius-2)' }}
                >
                  <MaterialIcon name="arrow_outward" size={16} color="var(--slate-9)" />
                </Flex>
              </Link>
            </Button>
          </Flex>
        </Box>
      )}
    </>
  );
}
