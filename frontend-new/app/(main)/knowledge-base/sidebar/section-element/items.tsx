'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { Box, Text, Button, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FolderIcon } from '@/app/components/ui';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import {
  ELEMENT_HEIGHT,
  TREE_INDENT_PER_LEVEL,
  TREE_BASE_PADDING,
} from '@/app/components/sidebar';
import { renderTreeLines } from './tree-lines';
import type {
  ConnectorItem,
  MoreConnectorLink,
} from '../../types';

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

/** Resolve an icon element based on the connector type */
function getConnectorItemIcon(connectorType: string, isSelected: boolean) {
  const iconColor = isSelected ? 'var(--accent-9)' : 'var(--slate-11)';
  const iconSize = 16;
  const iconStyle = { marginRight: 'var(--space-1)' };

  switch (connectorType) {
    case 'slack':
      return <MaterialIcon name="tag" size={iconSize} color={iconColor} style={iconStyle} />;
    case 'google-drive':
      return <FolderIcon variant="default" size={iconSize} color={iconColor} style={iconStyle} />;
    case 'jira':
      return <MaterialIcon name="bug_report" size={iconSize} color={iconColor} style={iconStyle} />;
    default:
      return <MaterialIcon name="description" size={iconSize} color={iconColor} style={iconStyle} />;
  }
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
        {getConnectorItemIcon(connectorType, isSelected)}
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

// ========================================
// MoreConnectorItem — navigates to connectors page with panel open
// ========================================

interface MoreConnectorItemProps {
  connector: MoreConnectorLink;
  href: string;
  onNavigate: (connectorTypeParam: string) => void;
}

export function MoreConnectorItem({ connector, href, onNavigate }: MoreConnectorItemProps) {
  const [isHovered, setIsHovered] = useState(false);

  const handleClick = (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    onNavigate(connector.connectorTypeParam);
  };

  return (
    <Link
      href={href}
      onClick={handleClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        height: `${ELEMENT_HEIGHT}px`,
        paddingLeft: 'var(--space-3)',
        paddingRight: 'var(--space-2)',
        boxSizing: 'border-box',
        backgroundColor: isHovered ? 'var(--slate-3)' : 'transparent',
        gap: 'var(--space-2)',
        borderRadius: 'var(--radius-1)',
        textDecoration: 'none',
        color: 'inherit',
        cursor: 'pointer',
      }}
    >
      <ConnectorIcon type={connector.type} size={16} color="var(--slate-11)" />
      <Text
        size="2"
        style={{
          color: 'var(--slate-11)',
          fontWeight: 400,
          fontStyle: 'normal',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          flex: 1,
          textAlign: 'left',
        }}
      >
        {connector.name}
      </Text>
      <Flex
        align="center"
        justify="center"
        style={{ width: '24px', height: '24px', pointerEvents: 'none', backgroundColor: 'var(--gray-a3)', borderRadius: 'var(--radius-2)' }}
      >
        <MaterialIcon name="arrow_outward" size={16} color="var(--slate-9)" />
      </Flex>
    </Link>
  );
}
