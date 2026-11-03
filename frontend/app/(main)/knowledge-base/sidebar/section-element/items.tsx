'use client';

import React, { useState } from 'react';
import { Box, Text, Button } from '@radix-ui/themes';
import { FolderIcon } from '@/app/components/ui';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import {
  ELEMENT_HEIGHT,
  TREE_INDENT_PER_LEVEL,
  TREE_BASE_PADDING,
} from '@/app/components/sidebar';
import { renderTreeLines } from './tree-lines';
import type { ConnectorItem } from '../../types';

// ========================================
// CollectionItem — flat collection in All Records mode
// ========================================

interface CollectionItemProps {
  collection: { id: string; name: string };
  isSelected: boolean;
  onSelect: () => void;
  depth?: number;
}

export function CollectionItem({ collection, isSelected, onSelect, depth = 1 }: CollectionItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const indent = depth * TREE_INDENT_PER_LEVEL;

  return (
    <Box
      style={{ position: 'relative', height: `${ELEMENT_HEIGHT}px` }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {renderTreeLines(depth, 1)}
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
        }}
      >
        <FolderIcon
          variant="default"
          size={16}
          color="var(--emerald-11)"
          style={{ marginRight: 'var(--space-1)' }}
        />
        <Text
          size="2"
          style={{
            color: isSelected ? 'var(--accent-11)' : 'var(--slate-11)',
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

// ========================================
// ConnectorItemComponent — connector item in All Records mode
// ========================================

interface ConnectorItemComponentProps {
  item: ConnectorItem;
  connectorType: string;
  isSelected: boolean;
  onSelect: () => void;
  depth?: number;
}

export function ConnectorItemComponent({
  item,
  connectorType,
  isSelected,
  onSelect,
  depth = 1,
}: ConnectorItemComponentProps) {
  const [isHovered, setIsHovered] = useState(false);
  const indent = depth * TREE_INDENT_PER_LEVEL;

  return (
    <Box
      style={{ position: 'relative', height: `${ELEMENT_HEIGHT}px` }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {renderTreeLines(depth)}
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
        }}
      >
        <ConnectorIcon
          type={connectorType}
          size={16}
          color={isSelected ? 'var(--accent-9)' : 'var(--slate-11)'}
          style={{ marginRight: 'var(--space-1)' }}
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
          {item.name}
        </Text>
      </Button>
    </Box>
  );
}
