'use client';

import React from 'react';
import { Flex, Box, Text, Button, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SECTION_PADDING_BOTTOM, SECTION_CONTENT_MARGIN_TOP, EMPTY_STATE_PADDING_X, EMPTY_STATE_PADDING_Y, FEATURED_ITEM_MARGIN_BOTTOM, ELEMENT_BORDER, SIDEBAR_COLLECTION_LIMIT } from '@/app/components/sidebar';
import { useTranslation } from 'react-i18next';
import { KBSectionHeader } from './section-header';
import { AppSection } from './section';
import { ConnectorItemComponent, MoreConnectorItem } from './section-element';
import type {
  Connector,
  ConnectorItem as ConnectorItemType,
  MoreConnectorLink,
  KnowledgeHubNode,
  NodeType,
  EnhancedFolderTreeNode,
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
  loadingAppIds: Set<string>;
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

  // Meatball menu actions
  onReindex?: (nodeId: string) => void;
  onRename?: (nodeId: string, newName: string) => Promise<void>;
  onDelete?: (nodeId: string) => void;

  // Overflow "More" panel
  onOpenMoreFolders?: (appId: string, appName: string, connector: string) => void;
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
  loadingAppIds,
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
  onReindex,
  onRename,
  onDelete,
  onOpenMoreFolders,
}: AllRecordsModeProps) {
  const { t } = useTranslation();

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
        const isKbApp = app.connector === 'KB';
        const appChildren = appChildrenCache.get(app.id) || [];
        // For the KB app, pass the categorized tree (shared + private) so that
        // sub-folder children populated by handleNodeExpand are visible in the tree.
        const categorizedTree = isKbApp
          ? [...(kbSharedTree || []), ...(kbPrivateTree || [])]
          : undefined;
        return (
          <AppSection
            key={app.id}
            app={app}
            childNodes={appChildren}
            isLoading={loadingAppIds.has(app.id)}
            onFolderSelect={(nodeType, nodeId) => {
              // For KB root-level collections, also update the selection state
              if (isKbApp && (nodeType === 'kb' || nodeType === 'recordGroup')) {
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
            onRename={onRename}
            onDelete={onDelete}
            maxVisible={SIDEBAR_COLLECTION_LIMIT}
            onMore={() => onOpenMoreFolders?.(app.id, app.name, app.connector || app.name)}
          />
        );
      })}

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

      {/* More Connectors section */}
      {moreConnectors.length > 0 && (
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
              <MoreConnectorItem key={connector.id} connector={connector} onNavigate={onNavigateToConnector} />
            ))}
            <Button
              variant="ghost"
              size="2"
              color="gray"
              onClick={onNavigateToConnectors}
              style={{
                width: '100%',
                justifyContent: 'space-between',
                paddingLeft: 'var(--space-3)',
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <MaterialIcon name="hub" size={16} color="var(--accent-11)" />
                <Text size="2" style={{ color: 'var(--slate-11)', fontWeight: 400, fontStyle: 'normal' }}>
                  {t('sidebar.seeMoreConnectors')}
                </Text>
              </div>
              <IconButton
                variant="soft"
                size="1"
                style={{ cursor: 'pointer', padding: '0px' }}
                onClick={(e) => {
                  e.stopPropagation();
                }}
              >
                <MaterialIcon name="arrow_outward" size={16} color="var(--slate-9)" />
              </IconButton>
            </Button>
          </Flex>
        </Box>
      )}
    </>
  );
}
