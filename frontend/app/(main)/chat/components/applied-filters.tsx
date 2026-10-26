'use client';

import React, { useMemo } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';
import type { NodeType } from '@/app/(main)/knowledge-base/types';
import type { AppliedFilterNode, AppliedFilters as AppliedFiltersData } from '../types';
import { useTranslation } from 'react-i18next';
import { BELOW_APP_KEYS, selectedNodeKind, type SelectedNodeKind } from '../utils/tree-selection';

interface ResolvedFilterNode {
  id: string;
  name: string;
  kind: SelectedNodeKind;
  /** The node as saved with the message: its type, connector and file type pick the icon. */
  node: AppliedFilterNode;
}

/** A filter saved before its node type was kept: the kind it was listed under stands in. */
function iconNodeType(item: ResolvedFilterNode): NodeType {
  if (item.node.nodeType) return item.node.nodeType as NodeType;
  if (item.kind === 'connector' || item.kind === 'collection') return 'app';
  return item.kind === 'folder' ? 'folder' : 'record';
}

interface AppliedFiltersProps {
  appliedFilters: AppliedFiltersData;
}

function toResolvedNodes(data: AppliedFiltersData, aloneLabel: (name: string) => string): ResolvedFilterNode[] {
  const nodes: ResolvedFilterNode[] = [];
  for (const item of data.apps ?? []) {
    nodes.push({ id: item.id, name: item.name, kind: item.nodeType === 'app' ? 'connector' : 'collection', node: item });
  }
  for (const item of data.kb ?? []) {
    nodes.push({ id: item.id, name: item.name, kind: 'collection', node: item });
  }
  for (const bucket of BELOW_APP_KEYS) {
    for (const item of data[bucket] ?? []) {
      nodes.push({
        id: item.id,
        name: bucket === 'recordsExact' ? aloneLabel(item.name) : item.name,
        kind: selectedNodeKind(bucket, item.nodeType),
        node: item,
      });
    }
  }
  return nodes;
}

export function AppliedFilters({ appliedFilters }: AppliedFiltersProps) {
  const { t } = useTranslation();
  const resolvedNodes = useMemo(
    () => toResolvedNodes(appliedFilters, (name) => t('chat.selectedAlone', { name })),
    [appliedFilters, t]
  );

  if (resolvedNodes.length === 0) return null;

  return (
    <Flex align="center" gap="2" wrap="wrap" style={{ marginTop: 'var(--space-2)' }}>
      {resolvedNodes.map((node) => (
        <Flex
          key={node.id}
          align="center"
          gap="1"
          style={{
            backgroundColor: 'var(--olive-a2)',
            border: '1px solid var(--slate-4)',
            borderRadius: 'var(--radius-full)',
            padding: '2px var(--space-2)',
            maxWidth: '180px',
            flexShrink: 0,
          }}
        >
          <KbNodeNameIcon
            isKnowledgeHub
            nodeType={iconNodeType(node)}
            connector={node.node.connector || null}
            extension={node.node.extension}
            mimeType={node.node.mimeType}
            name={node.node.name}
            size={14}
          />
          <Text
            size="1"
            style={{
              color: 'var(--slate-11)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {node.name}
          </Text>
        </Flex>
      ))}
    </Flex>
  );
}
