'use client';

// TODO remove after parity check with knowledge-base/sidebar
import React from 'react';
import { Flex, Box, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useKnowledgeBaseStore } from '../store';
import type { FolderTreeNode, KnowledgeBase } from '../types';

// Tree indentation constants
const TREE_INDENT_PER_LEVEL = 34; // Width of expand icon + folder icon + gap
const TREE_BASE_PADDING = 12;

interface FolderTreeItemProps {
  node: FolderTreeNode;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onToggle: (id: string) => void;
  expandedFolders: Record<string, boolean>;
}

function FolderTreeItem({
  node,
  isSelected,
  onSelect,
  onToggle,
  expandedFolders,
}: FolderTreeItemProps) {
  const isExpanded = !!expandedFolders[node.id];
  const hasChildren = node.children.length > 0;
  const indent = node.depth * TREE_INDENT_PER_LEVEL;

  return (
    <>
      <Button
        variant={isSelected ? 'soft' : 'ghost'}
        size="2"
        color={isSelected ? undefined : 'gray'}
        onClick={() => onSelect(node.id)}
        style={{
          width: '100%',
          justifyContent: 'flex-start',
          paddingLeft: `${TREE_BASE_PADDING + indent}px`,
        }}
      >
        {/* Expand/Collapse icon */}
        <Box
          style={{
            width: '16px',
            height: '16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginRight: 'var(--space-1)',
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
              color="var(--slate-11)"
            />
          )}
        </Box>

        {/* Folder icon */}
        <MaterialIcon
          name="folder"
          size={16}
          color={isSelected ? 'var(--accent-9)' : 'var(--slate-11)'}
          style={{ marginRight: 'var(--space-1)' }}
        />

        {/* Folder name */}
        <Text
          size="2"
          style={{
            color: isSelected ? 'var(--accent-11)' : 'var(--slate-12)',
            fontWeight: isSelected ? 500 : 400,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {node.name}
        </Text>
      </Button>

      {/* Children */}
      {isExpanded &&
        node.children.map((child) => (
          <FolderTreeItem
            key={child.id}
            node={child}
            isSelected={isSelected}
            onSelect={onSelect}
            onToggle={onToggle}
            expandedFolders={expandedFolders}
          />
        ))}
    </>
  );
}

interface KbSidebarProps {
  createdByYou: KnowledgeBase[];
  sharedWithYou: KnowledgeBase[];
  selectedKbId: string | null;
  onSelectKb: (id: string) => void;
  onBack: () => void;
}

export function KbSidebar({
  createdByYou,
  sharedWithYou,
  selectedKbId,
  onSelectKb,
  onBack,
}: KbSidebarProps) {
  const {
    folderTree,
    currentFolderId,
    expandedFolders,
    setCurrentFolderId,
    toggleFolderExpanded,
  } = useKnowledgeBaseStore();

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

  const createdTree = folderTree.length > 0 ? folderTree : buildTreeFromKbs(createdByYou);
  const sharedTree = buildTreeFromKbs(sharedWithYou);

  return (
    <Flex
      direction="column"
      style={{
        width: '233px',
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
          padding: 'var(--space-2)',
          height: '56px',
        }}
      >
        <Flex
          align="center"
          gap="2"
          style={{
            padding: 'var(--space-2)',
            cursor: 'pointer',
            borderRadius: 'var(--radius-2)',
          }}
          onClick={onBack}
        >
          <MaterialIcon name="arrow_back" size={24} color="var(--slate-11)" />
          <Flex align="center" gap="2">
            <MaterialIcon name="folder" size={24} color="var(--accent-9)" />
            <Text
              size="3"
              weight="medium"
              style={{ color: 'var(--slate-12)' }}
            >
              Knowledge Hub
            </Text>
          </Flex>
        </Flex>
      </Flex>

      {/* All button */}
      <Box style={{ padding: '0 var(--space-2)' }}>
        <Button
          variant={!selectedKbId ? 'soft' : 'ghost'}
          size="2"
          color={!selectedKbId ? undefined : 'gray'}
          onClick={() => onSelectKb('')}
          style={{ width: '100%', justifyContent: 'flex-start' }}
        >
          <MaterialIcon
            name="folder"
            size={16}
            color={!selectedKbId ? 'var(--accent-9)' : 'var(--slate-11)'}
          />
          <Text
            size="2"
            style={{
              color: !selectedKbId ? 'var(--accent-11)' : 'var(--slate-12)',
              fontWeight: !selectedKbId ? 500 : 400,
            }}
          >
            All
          </Text>
        </Button>
      </Box>

      {/* Scrollable content */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 'var(--space-4) var(--space-2)',
        }}
      >
        {/* Created By You section */}
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Text
            size="1"
            weight="medium"
            style={{
              color: 'var(--slate-9)',
              display: 'block',
              padding: 'var(--space-2) var(--space-3)',
              letterSpacing: '0.5px',
            }}
          >
            CREATED BY YOU
          </Text>
          <Flex direction="column" gap="1">
            {createdTree.map((node) => (
              <FolderTreeItem
                key={node.id}
                node={node}
                isSelected={selectedKbId === node.id || currentFolderId === node.id}
                onSelect={(id) => {
                  onSelectKb(id);
                  setCurrentFolderId(id);
                }}
                onToggle={toggleFolderExpanded}
                expandedFolders={expandedFolders}
              />
            ))}
          </Flex>
        </Box>

        {/* Shared With You section */}
        <Box>
          <Text
            size="1"
            weight="medium"
            style={{
              color: 'var(--slate-9)',
              display: 'block',
              padding: '8px 12px',
              letterSpacing: '0.5px',
            }}
          >
            SHARED WITH YOU
          </Text>
          <Flex direction="column" gap="1">
            {sharedTree.map((node) => (
              <FolderTreeItem
                key={node.id}
                node={node}
                isSelected={selectedKbId === node.id}
                onSelect={onSelectKb}
                onToggle={toggleFolderExpanded}
                expandedFolders={expandedFolders}
              />
            ))}
          </Flex>
        </Box>
      </Box>
    </Flex>
  );
}
