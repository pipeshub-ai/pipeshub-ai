'use client';

import React, { useState } from 'react';
import { Flex, Text, Checkbox } from '@radix-ui/themes';
import { KnowledgeItemIcon } from '@/app/components/ui/knowledge-item-icon';

interface CollectionRowProps {
  id: string;
  name: string;
  isSelected: boolean;
  onToggle: (id: string) => void;
  counts?: {
    folders: number;
    files: number;
  };
}

/**
 * A single selectable collection row with checkbox, folder icon, and name.
 */
export function CollectionRow({
  id,
  name,
  isSelected,
  onToggle,
  counts,
}: CollectionRowProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      justify="between"
      onClick={() => onToggle(id)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        height: 'var(--space-7)',
        backgroundColor: isHovered ? 'var(--slate-3)' : 'var(--slate-2)',
        border: '1px solid var(--slate-3)',
        borderRadius: 'var(--radius-1)',
        paddingLeft: 'var(--space-3)',
        paddingRight: 'var(--space-2)',
        cursor: 'pointer',
        transition: 'background-color 0.15s',
        flexShrink: 0,
      }}
    >
      {/* Left: checkbox + icon + name */}
      <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
        <Checkbox
          size="1"
          variant="classic"
          checked={isSelected}
          onCheckedChange={() => onToggle(id)}
          onClick={(e) => e.stopPropagation()}
          style={{ flexShrink: 0 }}
        />
        <KnowledgeItemIcon kind="collection" size={20} />
        <Text
          size="2"
          weight="medium"
          style={{
            color: 'var(--slate-11)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {name}
        </Text>
      </Flex>

      {/* Right: folder/file counts */}
      {counts && (
        <Text
          size="1"
          style={{
            color: 'var(--olive-9)',
            whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          {counts.folders} Folders & {counts.files} Files
        </Text>
      )}

    </Flex>
  );
}
