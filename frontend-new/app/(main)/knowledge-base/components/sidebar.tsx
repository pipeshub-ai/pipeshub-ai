'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Box, Text, Button, TextField, DropdownMenu, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { FolderIcon } from '@/app/components/ui';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useKnowledgeBaseStore } from '../store';
import { useTranslation } from 'react-i18next';
import type {
  PageViewMode,
  FolderTreeNode,
  KnowledgeBase,
  NodeType,
  EnhancedFolderTreeNode,
  Connector,
  ConnectorItem,
  MoreConnectorLink,
  ConnectorType,
  KnowledgeHubNode,
} from '../types';

// Sidebar width constant
const SIDEBAR_WIDTH = 233;

// Tree indentation constants
const TREE_INDENT_PER_LEVEL = 31;
const TREE_BASE_PADDING = 12;

// ========================================
// Shared Components
// ========================================

interface FolderTreeItemProps {
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
  showRootLines?: boolean; // When false, skips rendering the first-level vertical line (for app sections)
}

function FolderTreeItem({
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
  const editInputRef = useRef<HTMLInputElement>(null);

  const isExpanded = !!expandedFolders[node.id];
  const enhancedNode = node as EnhancedFolderTreeNode;
  const hasChildren = enhancedNode.hasChildren || node.children.length > 0;
  const isLoading = loadingNodeIds?.has(node.id);
  const indent = node.depth * TREE_INDENT_PER_LEVEL;

  // Show meatball menu for both shared and private section items
  const showMeatballMenu = (isHovered || isMenuOpen) && !isEditing;

  // Permission checks
  const canEdit = enhancedNode.permission?.canEdit !== false;
  const canDelete = enhancedNode.permission?.canDelete !== false;

  // Focus input when editing starts
  useEffect(() => {
    if (isEditing && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [isEditing]);

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

  const handleToggle = async () => {
    const willExpand = !isExpanded;

    // If expanding and node should have children but doesn't, fetch them
    if (
      willExpand &&
      enhancedNode.hasChildren &&
      node.children.length === 0 &&
      onNodeExpand
    ) {
      await onNodeExpand(node.id, enhancedNode.nodeType);
    }

    // Toggle expanded state
    onToggle(node.id);
  };

  // Render vertical tree lines for nested items
  const renderTreeLines = () => {
    if (node.depth === 0) return null;

    const lines = [];
    // When showRootLines is false, skip rendering the first vertical line (depth 0)
    // This is used for app sections in All Records mode to avoid root-level lines
    const startDepth = showRootLines ? 0 : 1;

    for (let i = startDepth; i < node.depth; i++) {
      lines.push(
        <Box
          key={`line-${i}`}
          style={{
            position: 'absolute',
            left: `${TREE_BASE_PADDING + (i * TREE_INDENT_PER_LEVEL) + 8}px`,
            top: 0,
            bottom: 0,
            width: '1px',
            backgroundColor: 'var(--slate-6)',
            pointerEvents: 'none',
          }}
        />
      );
    }
    return lines;
  };

  return (
    <>
      <Box
        style={{ position: 'relative', height: '32px' }}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {renderTreeLines()}
        <Button
          variant="ghost"
          size="2"
          color={'gray'}
          onClick={() => {
            onSelect(node.id);
            if (onNodeSelect && enhancedNode.nodeType) {
              onNodeSelect(enhancedNode.nodeType, node.id);
            }
          }}
          style={{
            width: '100%',
            justifyContent: 'flex-start',
            paddingLeft: `${TREE_BASE_PADDING + indent}px`,
            paddingRight: showMeatballMenu ? '32px' : '8px',
            cursor: 'pointer',
            backgroundColor: isSelected ? 'var(--olive-3)' : 'transparent',
          }}
        >
          {/* Chevron area - only render if hasChildren */}
          {hasChildren ? (
            <Box
              style={{
                width: '16px',
                height: '16px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                marginRight: '4px',
                cursor: 'pointer',
              }}
              onClick={(e) => {
                e.stopPropagation();
                handleToggle();
              }}
            >
              {isLoading ? (
                <LottieLoader variant="loader" size={16} />
              ) : (
                <MaterialIcon
                  name={isExpanded ? 'expand_more' : 'chevron_right'}
                  size={16}
                  color="var(--slate-11)"
                />
              )}
            </Box>
          ) : (
            // Spacer for leaf nodes to maintain alignment with siblings
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
              color={'var(--emerald-11)' }
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
                style={{
                  width: '100%',
                }}
              />
            </Box>
          ) : (
            <Text
              size="2"
              style={{
                color: isSelected ? 'var(--accent-11)' : 'var(--slate-12)',
                fontWeight: isSelected ? 500 : 400,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                textAlign: 'left',
                whiteSpace: 'nowrap',
                flex: 1,
                minWidth: 0,
              }}
            >
              {node.name}
            </Text>
          )}
        </Button>

        {/* Meatball menu */}
        {showMeatballMenu && (
          <Box
            style={{
              position: 'absolute',
              right: '8px',
              top: '50%',
              transform: 'translateY(-50%)',
            }}
          >
            <DropdownMenu.Root open={isMenuOpen} onOpenChange={setIsMenuOpen}>
              <DropdownMenu.Trigger>
                <IconButton
                  variant="ghost"
                  size="1"
                  color="gray"
                  onClick={(e) => e.stopPropagation()}
                  style={{ cursor: 'pointer' }}
                >
                  <MaterialIcon name="more_horiz" size={16} color="var(--slate-11)" />
                </IconButton>
              </DropdownMenu.Trigger>
              <DropdownMenu.Content size="1" style={{ minWidth: '120px' }}>
                <DropdownMenu.Item
                  onClick={(e) => {
                    e.stopPropagation();
                    onReindex?.(node.id);
                  }}
                >
                  <Flex align="center" gap="2">
                    <MaterialIcon name="refresh" size={16} color="var(--slate-11)" />
                    <Text size="2">{t('menu.reindex')}</Text>
                  </Flex>
                </DropdownMenu.Item>
                {canEdit && onRename && (
                  <DropdownMenu.Item
                    onClick={(e) => {
                      e.stopPropagation();
                      handleRenameStart();
                    }}
                  >
                    <Flex align="center" gap="2">
                      <MaterialIcon name="edit" size={16} color="var(--slate-11)" />
                      <Text size="2">{t('menu.rename')}</Text>
                    </Flex>
                  </DropdownMenu.Item>
                )}
                {canDelete && onDelete && (
                  <>
                    <DropdownMenu.Separator />
                    <DropdownMenu.Item
                      color="red"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete?.(node.id);
                      }}
                    >
                      <Flex align="center" gap="2">
                        <MaterialIcon name="delete" size={16} color="var(--red-9)" />
                        <Text size="2">{t('menu.delete')}</Text>
                      </Flex>
                    </DropdownMenu.Item>
                  </>
                )}
              </DropdownMenu.Content>
            </DropdownMenu.Root>
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
        ))
        }
    </>
  );
}

// ========================================
// All Records Mode Components
// ========================================

interface SectionHeaderProps {
  title: string;
  icon?: string;
  connectorType?: ConnectorType; // For connector sections - uses ConnectorIcon
  isExpanded: boolean;
  onToggle: () => void;
}

function SectionHeader({ title, icon, connectorType, isExpanded, onToggle }: SectionHeaderProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      gap="1"
      style={{
        padding: '4px 8px',
        cursor: 'pointer',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--slate-3)' : 'transparent',
      }}
      onClick={onToggle}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <MaterialIcon
        name={isExpanded ? 'expand_more' : 'chevron_right'}
        size={16}
        color="var(--slate-11)"
      />
      {connectorType ? (
        <ConnectorIcon type={connectorType} size={16} color="var(--slate-11)" />
      ) : icon ? (
        <MaterialIcon name={icon} size={16} color="var(--slate-11)" />
      ) : null}
      <Text
        size="2"
        weight="medium"
        style={{ color: 'var(--slate-12)', flex: 1 }}
      >
        {title}
      </Text>
    </Flex>
  );
}

interface CollectionItemProps {
  collection: EnhancedFolderTreeNode;
  isSelected: boolean;
  onSelect: () => void;
  depth?: number;
}

function CollectionItem({ collection, isSelected, onSelect, depth = 1 }: CollectionItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const indent = depth * TREE_INDENT_PER_LEVEL;

  // Render vertical tree lines for nested items
  const renderTreeLines = () => {
    if (depth === 0) return null;

    const lines = [];
    for (let i = 0; i < depth; i++) {
      lines.push(
        <Box
          key={`line-${i}`}
          style={{
            position: 'absolute',
            left: `${TREE_BASE_PADDING + (i * TREE_INDENT_PER_LEVEL) + 8}px`,
            top: 0,
            bottom: 0,
            width: '1px',
            backgroundColor: 'var(--slate-6)',
            pointerEvents: 'none',
          }}
        />
      );
    }
    return lines;
  };

  return (
    <Box
      style={{ position: 'relative' }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {renderTreeLines()}
      <Button
        variant="ghost"
        size="2"
        color={isSelected ? undefined : 'gray'}
        onClick={onSelect}
        style={{
          width: '100%',
          justifyContent: 'flex-start',
          paddingLeft: `${TREE_BASE_PADDING + indent}px`,
          backgroundColor: isHovered && !isSelected ? 'var(--slate-3)' : 'transparent',
          cursor: 'pointer',
        }}
      >
        <FolderIcon
          variant="default"
          size={16}
          color={isSelected ? 'var(--accent-9)' : 'var(--slate-11)'}
          style={{ marginRight: '4px' }}
        />
        <Text
          size="2"
          style={{
            color: isSelected ? 'var(--accent-11)' : 'var(--slate-12)',
            fontWeight: isSelected ? 500 : 400,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
            textAlign: 'left',
          }}
        >
          {collection.name}
        </Text>
      </Button>
    </Box>
  );
}

interface ConnectorItemComponentProps {
  item: ConnectorItem;
  connectorType: string;
  isSelected: boolean;
  onSelect: () => void;
  depth?: number;
}

function ConnectorItemComponent({
  item,
  connectorType,
  isSelected,
  onSelect,
  depth = 1,
}: ConnectorItemComponentProps) {
  const [isHovered, setIsHovered] = useState(false);
  const indent = depth * TREE_INDENT_PER_LEVEL;

  const getItemIcon = () => {
    const iconColor = isSelected ? 'var(--accent-9)' : 'var(--slate-11)';
    const iconSize = 16;
    const iconStyle = { marginRight: '4px' };

    if (connectorType === 'slack') {
      return <MaterialIcon name="tag" size={iconSize} color={iconColor} style={iconStyle} />;
    }
    if (connectorType === 'google-drive') {
      return <FolderIcon variant="default" size={iconSize} color={iconColor} style={iconStyle} />;
    }
    if (connectorType === 'jira') {
      return <MaterialIcon name="bug_report" size={iconSize} color={iconColor} style={iconStyle} />;
    }
    return <MaterialIcon name="description" size={iconSize} color={iconColor} style={iconStyle} />;
  };

  // Render vertical tree lines for nested items
  const renderTreeLines = () => {
    if (depth === 0) return null;

    const lines = [];
    for (let i = 0; i < depth; i++) {
      lines.push(
        <Box
          key={`line-${i}`}
          style={{
            position: 'absolute',
            left: `${TREE_BASE_PADDING + (i * TREE_INDENT_PER_LEVEL) + 8}px`,
            top: 0,
            bottom: 0,
            width: '1px',
            backgroundColor: 'var(--slate-6)',
            pointerEvents: 'none',
          }}
        />
      );
    }
    return lines;
  };

  return (
    <Box
      style={{ position: 'relative' }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {renderTreeLines()}
      <Button
        variant="ghost"
        size="2"
        color={isSelected ? undefined : 'gray'}
        onClick={onSelect}
        style={{
          width: '100%',
          justifyContent: 'flex-start',
          paddingLeft: `${TREE_BASE_PADDING + indent}px`,
          backgroundColor: isHovered && !isSelected ? 'var(--slate-3)' : 'transparent',
          cursor: 'pointer',
        }}
      >
        {getItemIcon()}
        <Text
          size="2"
          style={{
            color: isSelected ? 'var(--accent-11)' : 'var(--slate-12)',
            fontWeight: isSelected ? 500 : 400,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
            textAlign: 'left',
          }}
        >
          {item.name}
        </Text>
      </Button>
    </Box>
  );
}

interface MoreConnectorItemProps {
  connector: MoreConnectorLink;
}

function MoreConnectorItem({ connector }: MoreConnectorItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Button
      variant="ghost"
      size="2"
      color="gray"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        justifyContent: 'flex-start',
        paddingLeft: '12px',
        backgroundColor: isHovered ? 'var(--slate-3)' : 'transparent',
        cursor: 'pointer',
      }}
    >
      <ConnectorIcon type={connector.type} size={16} color="var(--slate-11)" />
      <Text
        size="2"
        style={{
          color: 'var(--slate-12)',
          fontWeight: 400,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          flex: 1,
          textAlign: 'left',
        }}
      >
        {connector.name}
      </Text>
      <MaterialIcon name="arrow_outward" size={14} color="var(--slate-9)" />
    </Button>
  );
}

// ========================================
// App Section Components (for All Records mode)
// ========================================

/**
 * Helper to get connector type from app name
 */
function getAppConnectorType(app: KnowledgeHubNode): ConnectorType {
  const name = app.name.toLowerCase();
  if (name.includes('drive')) return 'google-drive';
  if (name.includes('gmail')) return 'gmail';
  if (name.includes('slack')) return 'slack';
  if (name.includes('sharepoint')) return 'sharepoint';
  if (name.includes('outlook')) return 'outlook';
  if (name.includes('onedrive')) return 'onedrive';
  if (name.includes('teams')) return 'teams';
  if (name.includes('jira')) return 'jira';
  if (name.includes('confluence')) return 'confluence';
  if (name.includes('notion')) return 'notion';
  if (name.includes('dropbox')) return 'dropbox';
  if (name.includes('web')) return 'web';
  return 'generic';
}

/**
 * Convert KnowledgeHubNode to FolderTreeNode for use in FolderTreeItem
 */
function convertToTreeNode(node: KnowledgeHubNode, depth: number = 0): FolderTreeNode & { nodeType?: NodeType; hasChildren?: boolean } {
  return {
    id: node.id,
    name: node.name,
    depth,
    parentId: null,
    isExpanded: false,
    children: [],
    nodeType: node.nodeType,
    hasChildren: node.hasChildren,
  };
}

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
}

/**
 * AppSection - Renders each app as its own section (NOT grouped under connector)
 *
 * Design requirements:
 * - App header has NO expand chevron (always shows children)
 * - Children are folders/items inside the app (fetched via API)
 * - Children use the SAME tree structure as Collections (expand/collapse, tree lines)
 */
function AppSection({
  app,
  childNodes: children,
  isLoading,
  onFolderSelect,
  onFolderExpand,
  onToggleFolderExpanded,
  expandedFolders,
  loadingNodeIds,
  currentFolderId,
}: AppSectionProps) {
  const connectorType = getAppConnectorType(app);

  return (
    <Box style={{ marginBottom: '16px' }}>
      {/* App Header - NO expand chevron, always shows children */}
      <Flex
        align="center"
        gap="1"
        style={{
          padding: '4px 8px',
        }}
      >
        <ConnectorIcon type={connectorType} size={16} color="var(--slate-11)" />
        <Text
          size="2"
          weight="medium"
          style={{ color: 'var(--slate-12)', flex: 1 }}
        >
          {app.name}
        </Text>
      </Flex>

      {/* Children - same tree structure as Collections */}
      <Flex direction="column" gap="0" style={{ marginTop: '4px' }}>
        {isLoading ? (
          <Flex align="center" gap="2" style={{ padding: '8px 24px' }}>
            <LottieLoader variant="loader" size={16} />
          </Flex>
        ) : children.length > 0 ? (
          children.map((child) => (
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
                // Always toggle expansion state first
                onToggleFolderExpanded(id);
                // Then fetch children if needed
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
            />
          ))
        ) : (
          <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px 24px' }}>
            No items
          </Text>
        )}
      </Flex>
    </Box>
  );
}

// ========================================
// Main KB Sidebar Component
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
  flatCollections?: EnhancedFolderTreeNode[];
  appNodes?: KnowledgeHubNode[];
  appChildrenCache?: Map<string, KnowledgeHubNode[]>;
  loadingAppIds?: Set<string>;
  connectors?: Connector[];
  moreConnectors?: MoreConnectorLink[];
  isLoadingFlatCollections?: boolean;
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

export function Sidebar({
  pageViewMode,
  onBack,
  // Collections mode props
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
  // All Records mode props
  flatCollections = [],
  appNodes = [],
  appChildrenCache = new Map(),
  loadingAppIds = new Set(),
  connectors = [],
  navigationStack: _navigationStack = [],
  onNavigate: _onNavigate,
  onNavigateBack: _onNavigateBack,
  moreConnectors = [],
  isLoadingFlatCollections = false,
  // Sidebar item action handlers
  onSidebarReindex,
  onSidebarRename,
  onSidebarDelete,
  // All Records navigation handlers
  onAllRecordsSelectAll,
  onAllRecordsSelectCollection,
  onAllRecordsSelectApp: _onAllRecordsSelectApp,
  onAllRecordsSelectConnectorItem,
}: KBSidebarProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const {
    // Collections mode state
    currentFolderId,
    expandedFolders,
    setCurrentFolderId: _setCurrentFolderId,
    toggleFolderExpanded,
    // All Records mode state
    allRecordsSidebarSelection,
    expandedSections,
    setAllRecordsSidebarSelection,
    toggleSection,
  } = useKnowledgeBaseStore();

  const isCollectionsMode = pageViewMode === 'collections';

  // DEBUG: Log sidebar state
  console.log('DEBUG::sidebar::root', {
    pageViewMode,
    currentFolderId,
    selectedKbId,
    expandedFoldersCount: Object.keys(expandedFolders).length,
    expandedFolders: Object.keys(expandedFolders),
  });

  // Build folder tree from knowledge bases for demo
  const buildTreeFromKbs = (kbs: KnowledgeBase[]): FolderTreeNode[] => {
    return kbs.map((kb) => ({
      id: kb.id,
      name: kb.name,
      children: [],
      isExpanded: false,
      depth: 0,
      parentId: null,
    }));
  };

  // Collections mode tree
  const sharedTree = sharedTreeProp || buildTreeFromKbs(sharedWithYou);
  const privateTree = privateTreeProp || buildTreeFromKbs(privateCollections);

  // All Records mode helpers
  const isAllSelected = allRecordsSidebarSelection.type === 'all';
  const isCollectionSelected = (id: string) =>
    allRecordsSidebarSelection.type === 'collection' && allRecordsSidebarSelection.id === id;
  const isConnectorItemSelected = (connectorType: string, itemId: string) =>
    allRecordsSidebarSelection.type === 'connector' &&
    allRecordsSidebarSelection.connectorType === connectorType &&
    allRecordsSidebarSelection.itemId === itemId;

  const handleSelectAll = () => {
    // Update store state for UI selection highlighting
    setAllRecordsSidebarSelection({ type: 'all' });

    // Trigger URL navigation if handler provided
    if (onAllRecordsSelectAll) {
      onAllRecordsSelectAll();
    }
  };

  const handleSelectCollection = (collection: EnhancedFolderTreeNode) => {
    // Update store state for UI selection highlighting
    setAllRecordsSidebarSelection({
      type: 'collection',
      id: collection.id,
      name: collection.name,
    });

    // Trigger URL navigation if handler provided
    if (onAllRecordsSelectCollection) {
      onAllRecordsSelectCollection(collection.id);
    }
  };

  const handleSelectConnectorItem = (connectorType: string, item: ConnectorItem) => {
    // Update store state for UI selection highlighting
    setAllRecordsSidebarSelection({
      type: 'connector',
      connectorType: connectorType as ConnectorType,
      itemId: item.id,
      itemName: item.name,
    });

    // Trigger URL navigation if handler provided
    // Note: Connector items typically represent folders/channels, so we use 'folder' as nodeType
    if (onAllRecordsSelectConnectorItem) {
      onAllRecordsSelectConnectorItem('folder', item.id);
    }
  };

  const _getConnectorIcon = (type: string) => {
    switch (type) {
      case 'slack':
        return 'tag';
      case 'google-drive':
        return 'cloud';
      case 'jira':
        return 'bug_report';
      default:
        return 'extension';
    }
  };

  // ========================================
  // Render Collections Mode Sidebar
  // ========================================
  if (isCollectionsMode) {
    // Filter out app nodes at render time as an additional safety measure
    // Only filter if nodeType exists (EnhancedFolderTreeNode vs FolderTreeNode)
    const filteredSharedTree = sharedTree.filter(
      (node) => !('nodeType' in node) || node.nodeType !== 'app'
    );
    const filteredPrivateTree = privateTree.filter(
      (node) => !('nodeType' in node) || node.nodeType !== 'app'
    );

    return (
      <Flex
        direction="column"
        style={{
          width: `${SIDEBAR_WIDTH}px`,
          height: '100%',
          backgroundColor: 'var(--slate-1)',
          borderRight: '1px solid var(--slate-6)',
          flexShrink: 0,
        }}
      >
        {/* Header */}
        <Flex
          align="center"
          style={{
            padding: '8px',
            height: '56px',
          }}
        >
          <Flex
            align="center"
            gap="2"
            style={{
              padding: '8px',
              cursor: 'pointer',
              borderRadius: 'var(--radius-2)',
            }}
            onClick={onBack}
          >
            <MaterialIcon name="chevron_left" size={24} color="var(--slate-11)" />
            <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('nav.collections')}
            </Text>
          </Flex>
        </Flex>

        {/* Scrollable content */}
        <Box
          className="no-scrollbar"
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 8px',
          }}
        >
          {/* Shared section */}
          <Box style={{ marginBottom: '24px' }}>
            <Flex align="center" style={{ padding: '4px 8px' }}>
              <Text
                size="1"
                style={{
                  color: 'var(--slate-11)',
                  letterSpacing: '0.04px',
                  textTransform: 'uppercase',
                }}
              >
                {t('nav.shared')}
              </Text>
            </Flex>
            {isLoadingNodes && filteredSharedTree.length === 0 ? (
              <Flex align="center" gap="2" style={{ padding: '4px 8px' }}>
                <LottieLoader variant="loader" size={16} />
              </Flex>
            ) : filteredSharedTree.length > 0 ? (
              <Flex direction="column" gap="0">
                {filteredSharedTree.map((node) => (
                  <FolderTreeItem
                    key={node.id}
                    node={node}
                    isSelected={currentFolderId === node.id || selectedKbId === node.id}
                    currentFolderId={currentFolderId}
                    onSelect={(id) => onSelectKb?.(id)}
                    onToggle={toggleFolderExpanded}
                    expandedFolders={expandedFolders}
                    loadingNodeIds={loadingNodeIds}
                    onNodeExpand={onNodeExpand}
                    onNodeSelect={onNodeSelect}
                    sectionType="shared"
                    onReindex={onSidebarReindex}
                    onRename={onSidebarRename}
                    onDelete={onSidebarDelete}
                  />
                ))}
              </Flex>
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
                {t('sidebar.sharedWithYou')}
              </Text>
            )}
          </Box>

          {/* Private section */}
          <Box>
            <Flex align="center" justify="between" style={{ padding: '4px 8px' }}>
              <Text
                size="1"
                style={{ color: 'var(--slate-11)', letterSpacing: '0.04px' }}
              >
                {t('nav.private')}
              </Text>
              {onAddPrivate && (
                <Box onClick={onAddPrivate} style={{ cursor: 'pointer' }}>
                  <MaterialIcon name="add" size={16} color="var(--slate-11)" />
                </Box>
              )}
            </Flex>
            {isLoadingNodes && filteredPrivateTree.length === 0 ? (
              <Flex align="center" gap="2" style={{ padding: '4px 8px' }}>
                <LottieLoader variant="loader" size={16} />
              </Flex>
            ) : filteredPrivateTree.length > 0 ? (
              <Flex direction="column" gap="0">
                {filteredPrivateTree.map((node) => (
                  <FolderTreeItem
                    key={node.id}
                    node={node}
                    isSelected={currentFolderId === node.id || selectedKbId === node.id}
                    currentFolderId={currentFolderId}
                    onSelect={(id) => onSelectKb?.(id)}
                    onToggle={toggleFolderExpanded}
                    expandedFolders={expandedFolders}
                    loadingNodeIds={loadingNodeIds}
                    onNodeExpand={onNodeExpand}
                    onNodeSelect={onNodeSelect}
                    sectionType="private"
                    onReindex={onSidebarReindex}
                    onRename={onSidebarRename}
                    onDelete={onSidebarDelete}
                  />
                ))}
              </Flex>
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
                {t('sidebar.noPrivateYet')}
              </Text>
            )}
          </Box>
        </Box>
      </Flex>
    );
  }

  // ========================================
  // Render All Records Mode Sidebar
  // ========================================
  return (
    <Flex
      direction="column"
      style={{
        width: `${SIDEBAR_WIDTH}px`,
        height: '100%',
        backgroundColor: 'var(--slate-1)',
        borderRight: '1px solid var(--slate-6)',
        flexShrink: 0,
      }}
    >
      {/* Header */}
      <Flex
        align="center"
        style={{
          padding: '8px',
          height: '56px',
        }}
      >
        <Flex
          align="center"
          gap="2"
          style={{
            padding: '8px',
            cursor: 'pointer',
            borderRadius: 'var(--radius-2)',
          }}
          onClick={onBack}
        >
          <MaterialIcon name="chevron_left" size={24} color="var(--slate-11)" />
          <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('nav.allRecords')}
          </Text>
        </Flex>
      </Flex>

      {/* Search field */}
      {/* <Box style={{ padding: '0 8px 8px' }}>
        <TextField.Root size="2" placeholder={t('nav.all')} style={{ width: '100%' }}>
          <TextField.Slot side="left">
            <MaterialIcon name="search" size={16} color="var(--slate-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Box> */}

      {/* Scrollable content */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '8px',
        }}
      >
        {/* "All" item */}
        <Button
          variant={'soft'}
          size="2"
          color={isAllSelected ? undefined : 'gray'}
          onClick={handleSelectAll}
          style={{
            width: '100%',
            justifyContent: 'flex-start',
            marginBottom: '8px',
            ...{ border: '1px solid var(--slate-3)' },
          }}
        >
          <Text
            size="2"
            style={{
              color: 'var(--accent-11)',
              fontWeight:500,
            }}
          >
            {t('nav.all')}
          </Text>
        </Button>

        {/* Collections section */}
        <Box style={{ marginBottom: '16px' }}>
          <SectionHeader
            title={t('nav.collections')}
            isExpanded={expandedSections.has('collections')}
            onToggle={() => toggleSection('collections')}
          />
          {expandedSections.has('collections') && (
            <Flex direction="column" gap="0" style={{ marginTop: '4px' }}>
              {isLoadingFlatCollections ? (
                <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px 24px' }}>
                  {t('action.loading')}
                </Text>
              ) : flatCollections.length > 0 ? (
                flatCollections.map((collection) => (
                  <CollectionItem
                    key={collection.id}
                    collection={collection}
                    isSelected={isCollectionSelected(collection.id)}
                    onSelect={() => handleSelectCollection(collection)}
                  />
                ))
              ) : (
                <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px 24px' }}>
                  {t('message.noCollections')}
                </Text>
              )}
            </Flex>
          )}
        </Box>

        {/* App sections - each app is its own section (NOT grouped by connector) */}
        {appNodes.map((app) => (
          <AppSection
            key={app.id}
            app={app}
            childNodes={appChildrenCache.get(app.id) || []}
            isLoading={loadingAppIds.has(app.id)}
            onFolderSelect={(nodeType, nodeId) => onAllRecordsSelectConnectorItem?.(nodeType, nodeId)}
            onFolderExpand={async (_nodeId, _nodeType) => {
              // Fetch children if not already cached
              // Note: toggle is handled by onToggleFolderExpanded
            }}
            onToggleFolderExpanded={toggleFolderExpanded}
            expandedFolders={expandedFolders}
            loadingNodeIds={loadingNodeIds}
            currentFolderId={currentFolderId}
          />
        ))}

        {/* Legacy connector sections (deprecated - kept for backward compatibility) */}
        {connectors.map((connector) => (
          <Box key={connector.id} style={{ marginBottom: '16px' }}>
            <SectionHeader
              title={connector.name}
              connectorType={connector.type}
              isExpanded={expandedSections.has(connector.type)}
              onToggle={() => toggleSection(connector.type)}
            />
            {expandedSections.has(connector.type) && (
              <Flex direction="column" gap="0" style={{ marginTop: '4px' }}>
                {connector.items.length > 0 ? (
                  connector.items.map((item) => (
                    <ConnectorItemComponent
                      key={item.id}
                      item={item}
                      connectorType={connector.type}
                      isSelected={isConnectorItemSelected(connector.type, item.id)}
                      onSelect={() => handleSelectConnectorItem(connector.type, item)}
                    />
                  ))
                ) : (
                  <Text size="1" style={{ color: 'var(--slate-9)', padding: '8px 24px' }}>
                    {t('message.noItems')}
                  </Text>
                )}
              </Flex>
            )}
          </Box>
        ))}

        {/* More Connectors section */}
        {moreConnectors.length > 0 && (
          <Box style={{ marginTop: '16px' }}>
            
            <Text
              size="1"
              style={{
                color: 'var(--slate-11)',
                letterSpacing: '0.04px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '4px 0px',
                marginBottom: '8px',
              }}
            >
              <MaterialIcon name="hub" size={16} color="var(--accent-11)" />
              {t('nav.moreConnectors')}
            </Text>
            <Flex direction="column" gap="2" style={{ marginTop: '4px' }}>
              {moreConnectors.map((connector) => (
                <MoreConnectorItem key={connector.id} connector={connector} />
              ))}
              <Button
                variant="ghost"
                size="2"
                color="gray"
                onClick={() => router.push('/connectors')}
                style={{
                  width: '100%',
                  justifyContent: 'space-between',
                  paddingLeft: '12px',
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <MaterialIcon name="hub" size={16} color="var(--accent-11)" />
                <Text size="2" style={{ color: 'var(--slate-12)', fontWeight: 400 }}>
                  {t('sidebar.seeMoreConnectors')}
                </Text>
                </div>
                <MaterialIcon name="arrow_outward" size={14} color="var(--slate-9)" />
              </Button>
            </Flex>
          </Box>
        )}
      </Box>
    </Flex>
  );
}
