'use client';

import React, { useState } from 'react';
import { Flex, Text, Checkbox } from '@radix-ui/themes';
import { KnowledgeItemIcon } from '@/app/components/ui/knowledge-item-icon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import { AGENT_KNOWLEDGE_FALLBACK_ICON } from '@/app/(main)/agents/agent-builder/display-utils';

interface CollectionRowProps {
  id: string;
  name: string;
  /** Knowledge graph entry `type` (e.g. `KB`, `Jira`) — selects row icon. */
  sourceType?: string;
  isSelected: boolean;
  onToggle: (id: string) => void;
  counts?: {
    folders: number;
    files: number;
  };
}

const CHECKBOX_ALIGN: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
  lineHeight: 0,
};

function CollectionLeadingIcon({ sourceType, size = 20 }: { sourceType?: string; size?: number }) {
  if (!sourceType?.trim()) {
    return <KnowledgeItemIcon kind="collection" size={size} />;
  }
  const normalized = sourceType.trim();
  if (normalized.toUpperCase() === 'KB') {
    return (
      <ThemeableAssetIcon
        src={AGENT_KNOWLEDGE_FALLBACK_ICON}
        size={size}
        {...themeableAssetIconPresets.agentBuilderCategoryRow}
      />
    );
  }
  return <ConnectorIcon type={resolveConnectorType(normalized)} size={size} />;
}

/**
 * A single selectable collection row with checkbox, folder icon, and name.
 */
export function CollectionRow({
  id,
  name,
  sourceType,
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
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
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
        <span style={CHECKBOX_ALIGN}>
          <Checkbox
            size="1"
            variant="classic"
            checked={isSelected}
            onCheckedChange={() => onToggle(id)}
            onClick={(e) => e.stopPropagation()}
          />
        </span>
        <CollectionLeadingIcon sourceType={sourceType} size={20} />
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
