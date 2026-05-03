'use client';

import React, { useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui/ConnectorIcon';
import { SECTION_HEADER_PADDING, ELEMENT_HEIGHT, HOVER_BACKGROUND } from '@/app/components/sidebar';
import type { ConnectorType } from '../types';

interface KBSectionHeaderProps {
  title: string;
  icon?: string;
  connectorType?: ConnectorType;
  isExpanded: boolean;
  onToggle: () => void;
  /** Custom height for the section header. Defaults to ELEMENT_HEIGHT (32px). */
  height?: number;
}

/**
 * Collapsible section header with chevron, icon, and title.
 * Used for Collections, connector groups, and app sections.
 */
export function KBSectionHeader({ title, icon, connectorType, isExpanded, onToggle, height = ELEMENT_HEIGHT }: KBSectionHeaderProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      gap="2"
      style={{
        height: `${height}px`,
        padding: SECTION_HEADER_PADDING,
        cursor: 'pointer',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? HOVER_BACKGROUND : 'transparent',
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
