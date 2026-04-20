'use client';

import React, { useState, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Box, Text, TextField } from '@radix-ui/themes';
import { SidebarBase, SidebarBackHeader, SecondaryPanel } from '@/app/components/sidebar';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { buildConnectorsUrl } from '@/app/(main)/workspace/connectors/utils/build-connectors-url';
import { useKnowledgeBaseStore } from '../store';
import { useTranslation } from 'react-i18next';
import { CollectionsMode } from './collections-mode';
import { AllRecordsMode } from './all-records-mode';
import { convertToTreeNode } from './section';
import { FolderTreeItem, CollectionItem } from './section-element';
import type {
  PageViewMode,
  KnowledgeBase,
  NodeType,
  EnhancedFolderTreeNode,
  Connector,
  MoreConnectorLink,
  ConnectorType,
  KnowledgeHubNode,
} from '../types';

// ========================================
// Props
// ========================================

interface KBSidebarProps {
  pageViewMode: PageViewMode;
  onBack: () => void;

  // Collections mode props
  createdByYou?: KnowledgeBase[];
  sharedWithYou?: KnowledgeBase[];
  privateCollections?: KnowledgeBase[];
  selectedKbId?: string | null;
  onSelectKb?: (id: string) => void;
  onAddPrivate?: () => void;
  onNodeExpand?: (nodeId: string, nodeType: NodeType) => Promise<void>;
  onNodeSelect?: (nodeType: string, nodeId: string) => void;
  isLoadingNodes?: boolean;
  loadingNodeIds?: Set<string>;
  sharedTree?: EnhancedFolderTreeNode[];
  privateTree?: EnhancedFolderTreeNode[];

  // All Records mode props
  appNodes?: KnowledgeHubNode[];
  appChildrenCache?: Map<string, KnowledgeHubNode[]>;
  loadingAppIds?: Set<string>;
  connectors?: Connector[];
  moreConnectors?: MoreConnectorLink[];
  navigationStack?: Array<{ nodeType: string; nodeId: string; nodeName: string }>;
  onNavigate?: (node: { nodeType: string; nodeId: string; nodeName: string }) => void;
  onNavigateBack?: () => void;

  // Sidebar item action handlers
  onSidebarReindex?: (nodeId: string) => void;
  onSidebarRename?: (nodeId: string, newName: string) => Promise<void>;
  onSidebarDelete?: (nodeId: string) => void;

  // All Records navigation handlers
  onAllRecordsSelectAll?: () => void;
  onAllRecordsSelectCollection?: (collectionId: string) => void;
  onAllRecordsSelectApp?: (appId: string) => void;
  onAllRecordsSelectConnectorItem?: (nodeType: string, nodeId: string) => void;
}

// ========================================
// Main Sidebar Component
// ========================================

export default function KnowledgeBaseSidebar(props?: KBSidebarProps) {
  if (!props) {
    return <KBSidebarFromStore />;
  }
  return <KBSidebarContent {...props} />;
}

/**
 * Store-driven sidebar — reads state from Zustand store.
 * Used when rendered via @sidebar parallel route slot.
 */
function KBSidebarFromStore() {
  const { t } = useTranslation();
  const router = useRouter();
  const store = useKnowledgeBaseStore();
  const pageViewMode = store.currentViewMode || 'collections';

  const handleBack = () => {
    router.push('/chat');
  };

  const title = pageViewMode === 'collections' ? t('nav.collections') : t('nav.allRecords');

  return (
    <SidebarBase header={<SidebarBackHeader title={title} onBack={handleBack} />}>
      <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px' }}>
        {t('action.loading')}
      </Text>
    </SidebarBase>
  );
}

/**
 * Props-driven sidebar — receives all data from the page.
 * Delegates to CollectionsMode or AllRecordsMode.
 * Uses SidebarBase's secondaryPanel prop for "More Collections" panel.
 */
function KBSidebarContent({
  pageViewMode,
  onBack,
  createdByYou: _createdByYou = [],
  sharedWithYou = [],
  privateCollections = [],
  selectedKbId,
  onSelectKb,
  onAddPrivate,
  onNodeExpand,
  onNodeSelect,
  isLoadingNodes = false,
  loadingNodeIds = new Set(),
  sharedTree: sharedTreeProp,
  privateTree: privateTreeProp,
  appNodes = [],
  appChildrenCache = new Map(),
  loadingAppIds = new Set(),
  connectors = [],
  navigationStack: _navigationStack = [],
  onNavigate: _onNavigate,
  onNavigateBack: _onNavigateBack,
  moreConnectors = [],
  onSidebarReindex,
  onSidebarRename,
  onSidebarDelete,
  onAllRecordsSelectAll,
  onAllRecordsSelectCollection,
  onAllRecordsSelectApp: _onAllRecordsSelectApp,
  onAllRecordsSelectConnectorItem,
}: KBSidebarProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const {
    currentFolderId,
    expandedFolders,
    toggleFolderExpanded,
    allRecordsSidebarSelection,
    expandedSections,
    setAllRecordsSidebarSelection,
    toggleSection,
  } = useKnowledgeBaseStore();

  // Local state for secondary panel (replaces store-based MoreCollections)
  const [isSecondaryPanelOpen, setIsSecondaryPanelOpen] = useState(false);
  const [secondaryPanelSectionType, setSecondaryPanelSectionType] = useState<'shared' | 'private' | null>(null);
  const [secondaryPanelSearchQuery, setSecondaryPanelSearchQuery] = useState('');

  // State for "More Folders" panel in All Records mode (connector overflow)
  const [moreFoldersApp, setMoreFoldersApp] = useState<{
    appId: string;
    appName: string;
    connector: string;
  } | null>(null);

  const isCollectionsMode = pageViewMode === 'collections';
  const headerTitle = isCollectionsMode ? t('nav.collections') : t('nav.allRecords');

  // Build folder tree from knowledge bases (fallback when tree prop is not provided)
  const buildTreeFromKbs = (kbs: KnowledgeBase[]): EnhancedFolderTreeNode[] => {
    return kbs.map((kb) => ({
      id: kb.id,
      name: kb.name,
      children: [],
      isExpanded: false,
      depth: 0,
      parentId: null,
      nodeType: 'kb' as NodeType,
      hasChildren: false,
    }));
  };

  const sharedTree: EnhancedFolderTreeNode[] = sharedTreeProp || buildTreeFromKbs(sharedWithYou ?? []);
  const privateTree: EnhancedFolderTreeNode[] = privateTreeProp || buildTreeFromKbs(privateCollections ?? []);

  // All Records mode helpers
  const isAllSelected = allRecordsSidebarSelection.type === 'all';
  const isCollectionSelected = (id: string) =>
    allRecordsSidebarSelection.type === 'collection' && allRecordsSidebarSelection.id === id;
  const isConnectorItemSelected = (connectorType: string, itemId: string) =>
    allRecordsSidebarSelection.type === 'connector' &&
    allRecordsSidebarSelection.connectorType === connectorType &&
    allRecordsSidebarSelection.itemId === itemId;

  // Precompute flat collection list for the All Records secondary panel once,
  // rather than recreating it on every render inside buildSecondaryPanel.
  const allSecondaryPanelCollections = useMemo(
    () =>
      appNodes
        .flatMap((app) => appChildrenCache?.get(app.id) ?? [])
        .map((n) => ({ id: n.id, name: n.name })),
    [appNodes, appChildrenCache]
  );

  const handleSelectAll = () => {
    setAllRecordsSidebarSelection({ type: 'all' });
    onAllRecordsSelectAll?.();
  };

  const handleSelectCollection = (collection: { id: string; name: string }) => {
    setAllRecordsSidebarSelection({
      type: 'collection',
      id: collection.id,
      name: collection.name,
    });
    onAllRecordsSelectCollection?.(collection.id);
  };

  const handleSelectConnectorItem = (connectorType: string, item: { id: string; name: string }) => {
    setAllRecordsSidebarSelection({
      type: 'connector',
      connectorType: connectorType as ConnectorType,
      itemId: item.id,
      itemName: item.name,
    });
    onAllRecordsSelectConnectorItem?.('folder', item.id);
  };

  // Handler for opening "More Folders" panel from connector sections
  const handleOpenMoreFolders = (appId: string, appName: string, connector: string) => {
    setMoreFoldersApp({ appId, appName, connector });
    setIsSecondaryPanelOpen(true);
    setSecondaryPanelSectionType(null);
  };

  // Secondary panel dismiss handler
  const handleDismissSecondaryPanel = () => {
    setIsSecondaryPanelOpen(false);
    setSecondaryPanelSearchQuery('');
    setSecondaryPanelSectionType(null);
    setMoreFoldersApp(null);
  };

  // Build secondary panel content (More Collections or More Folders)
  const buildSecondaryPanel = () => {
    if (!isSecondaryPanelOpen) return undefined;

    // "More Folders" panel for All Records connector overflow
    if (moreFoldersApp) {
      const { appId, appName } = moreFoldersApp;
      const appNode = appNodes.find((a) => a.id === appId);
      const isKbApp = appNode?.connector === 'KB';
      const appChildren = appChildrenCache.get(appId) || [];
      const categorizedTree = isKbApp
        ? [...(sharedTree || []), ...(privateTree || [])]
        : undefined;

      return (
        <SecondaryPanel
          header={
            <SidebarBackHeader
              title={appName}
              onBack={handleDismissSecondaryPanel}
            />
          }
        >
          <Flex direction="column" gap="0">
            {categorizedTree ? (
              categorizedTree.length > 0 ? (
                categorizedTree.map((node) => (
                  <FolderTreeItem
                    key={node.id}
                    node={node}
                    isSelected={currentFolderId === node.id}
                    currentFolderId={currentFolderId}
                    onSelect={(id) => onAllRecordsSelectConnectorItem?.(node.nodeType, id)}
                    onToggle={(id) => {
                      toggleFolderExpanded(id);
                      if (node.hasChildren) {
                        onNodeExpand?.(id, node.nodeType as NodeType);
                      }
                    }}
                    expandedFolders={expandedFolders}
                    loadingNodeIds={loadingNodeIds}
                    onNodeExpand={onNodeExpand}
                    onNodeSelect={onAllRecordsSelectConnectorItem}
                    showRootLines={false}
                    onReindex={onSidebarReindex}
                    onRename={onSidebarRename}
                    onDelete={onSidebarDelete}
                  />
                ))
              ) : (
                <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px 24px' }}>
                  No items
                </Text>
              )
            ) : appChildren.length > 0 ? (
              appChildren.map((child) => (
                <FolderTreeItem
                  key={child.id}
                  node={convertToTreeNode(child, 1)}
                  isSelected={currentFolderId === child.id}
                  currentFolderId={currentFolderId}
                  onSelect={(id) => {
                    const childNode = appChildren.find((c) => c.id === id);
                    if (childNode) {
                      onAllRecordsSelectConnectorItem?.(childNode.nodeType, id);
                    }
                  }}
                  onToggle={(id) => {
                    toggleFolderExpanded(id);
                    const childNode = appChildren.find((c) => c.id === id);
                    if (childNode?.hasChildren) {
                      onNodeExpand?.(id, childNode.nodeType);
                    }
                  }}
                  expandedFolders={expandedFolders}
                  loadingNodeIds={loadingNodeIds}
                  onNodeExpand={onNodeExpand}
                  onNodeSelect={onAllRecordsSelectConnectorItem}
                  showRootLines={false}
                  onReindex={onSidebarReindex}
                  onRename={onSidebarRename}
                  onDelete={onSidebarDelete}
                />
              ))
            ) : (
              <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px 24px' }}>
                No items
              </Text>
            )}
          </Flex>
        </SecondaryPanel>
      );
    }

    // Filter by search query
    const filterByName = <T extends { name: string }>(items: T[]): T[] => {
      if (!secondaryPanelSearchQuery.trim()) return items;
      const query = secondaryPanelSearchQuery.toLowerCase().trim();
      return items.filter((item) => item.name.toLowerCase().includes(query));
    };

    // Select items and handlers based on mode
    const handleSelectKb = (id: string) => {
      onSelectKb?.(id);
      handleDismissSecondaryPanel();
    };

    const handleNodeSelect = (nodeType: string, nodeId: string) => {
      onNodeSelect?.(nodeType, nodeId);
      handleDismissSecondaryPanel();
    };

    const handlePanelSelectCollection = (collection: { id: string; name: string }) => {
      handleSelectCollection(collection);
      handleDismissSecondaryPanel();
    };

    return (
      <SecondaryPanel
        header={
          <SidebarBackHeader
            title={t('sidebar.moreCollections')}
            onBack={handleDismissSecondaryPanel}
          />
        }
      >
        {/* Search */}
        <Box style={{ padding: '0 0 8px', flexShrink: 0 }}>
          <TextField.Root
            size="2"
            placeholder={t('sidebar.searchCollections')}
            value={secondaryPanelSearchQuery}
            onChange={(e) => setSecondaryPanelSearchQuery(e.target.value)}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--slate-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Box>

        {/* Collection list */}
        {isCollectionsMode ? (
          <Flex direction="column" gap="0">
            {(() => {
              const nodes = secondaryPanelSectionType === 'private' ? privateTree : sharedTree;
              const filtered = nodes.filter(
                (node) => !('nodeType' in node) || node.nodeType !== 'app'
              );
              return filterByName(filtered).map((node) => (
                <FolderTreeItem
                  key={node.id}
                  node={node}
                  isSelected={currentFolderId === node.id || selectedKbId === node.id}
                  currentFolderId={currentFolderId}
                  onSelect={handleSelectKb}
                  onToggle={toggleFolderExpanded}
                  expandedFolders={expandedFolders}
                  loadingNodeIds={loadingNodeIds}
                  onNodeExpand={onNodeExpand}
                  onNodeSelect={handleNodeSelect}
                  sectionType={secondaryPanelSectionType === 'private' ? 'private' : 'shared'}
                  onReindex={onSidebarReindex}
                  onRename={onSidebarRename}
                  onDelete={onSidebarDelete}
                />
              ));
            })()}
          </Flex>
        ) : (
          <Flex direction="column" gap="0">
            {filterByName(allSecondaryPanelCollections).map((collection) => (
              <CollectionItem
                key={collection.id}
                collection={collection}
                isSelected={isCollectionSelected(collection.id)}
                onSelect={() => handlePanelSelectCollection(collection)}
              />
            ))}
          </Flex>
        )}
      </SecondaryPanel>
    );
  };

  return (
    <SidebarBase
      header={<SidebarBackHeader title={headerTitle} onBack={onBack} />}
      secondaryPanel={buildSecondaryPanel()}
      onDismissSecondaryPanel={isSecondaryPanelOpen ? handleDismissSecondaryPanel : undefined}
    >
      {isCollectionsMode ? (
        <CollectionsMode
          sharedTree={sharedTree}
          privateTree={privateTree}
          currentFolderId={currentFolderId}
          selectedKbId={selectedKbId}
          onSelectKb={onSelectKb}
          onAddPrivate={onAddPrivate}
          expandedFolders={expandedFolders}
          onToggle={toggleFolderExpanded}
          loadingNodeIds={loadingNodeIds}
          isLoading={isLoadingNodes}
          onNodeExpand={onNodeExpand}
          onNodeSelect={onNodeSelect}
          onReindex={onSidebarReindex}
          onRename={onSidebarRename}
          onDelete={onSidebarDelete}
        />
      ) : (
        <AllRecordsMode
          isAllSelected={isAllSelected}
          isConnectorItemSelected={isConnectorItemSelected}
          onSelectAll={handleSelectAll}
          onSelectCollection={handleSelectCollection}
          onSelectConnectorItem={handleSelectConnectorItem}
          appNodes={appNodes}
          appChildrenCache={appChildrenCache}
          loadingAppIds={loadingAppIds}
          connectors={connectors}
          moreConnectors={moreConnectors}
          expandedSections={expandedSections}
          onToggleSection={toggleSection}
          expandedFolders={expandedFolders}
          onToggleFolderExpanded={toggleFolderExpanded}
          loadingNodeIds={loadingNodeIds}
          currentFolderId={currentFolderId}
          onFolderSelect={onAllRecordsSelectConnectorItem}
          onNodeExpand={onNodeExpand}
          kbSharedTree={sharedTree}
          kbPrivateTree={privateTree}
          onNavigateToConnectors={() => router.push(buildConnectorsUrl(isAdmin))}
          onNavigateToConnector={(connectorTypeParam) =>
            router.push(buildConnectorsUrl(isAdmin, connectorTypeParam))
          }
          onReindex={onSidebarReindex}
          onRename={onSidebarRename}
          onDelete={onSidebarDelete}
          onOpenMoreFolders={handleOpenMoreFolders}
        />
      )}
    </SidebarBase>
  );
}
