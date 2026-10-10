'use client';

import React, { useMemo } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { KnowledgeItemIcon } from '@/app/components/ui/knowledge-item-icon';
import { resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import type { AppliedFilters as AppliedFiltersData } from '../types';
import { useTranslation } from 'react-i18next';
import { BELOW_APP_KEYS, selectedNodeKind, type SelectedNodeKind } from '../utils/tree-selection';

interface ResolvedFilterNode {
  id: string;
  name: string;
  kind: SelectedNodeKind;
  connectorType?: string;
}

interface AppliedFiltersProps {
  appliedFilters: AppliedFiltersData;
}

function toResolvedNodes(data: AppliedFiltersData, aloneLabel: (name: string) => string): ResolvedFilterNode[] {
  const nodes: ResolvedFilterNode[] = [];
  for (const item of data.apps ?? []) {
    const isConnector = item.nodeType === 'app';
    nodes.push({
      id: item.id,
      name: item.name,
      kind: isConnector ? 'connector' : 'collection',
      connectorType: isConnector && item.connector
        ? resolveConnectorType(item.connector)
        : undefined,
    });
  }
  for (const item of data.kb ?? []) {
    nodes.push({
      id: item.id,
      name: item.name,
      kind: 'collection',
      connectorType: item.connector ? resolveConnectorType(item.connector) : undefined,
    });
  }
  for (const bucket of BELOW_APP_KEYS) {
    for (const item of data[bucket] ?? []) {
      nodes.push({
        id: item.id,
        name: bucket === 'recordsExact' ? aloneLabel(item.name) : item.name,
        kind: selectedNodeKind(bucket, item.nodeType),
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
          <KnowledgeItemIcon
            kind={node.kind}
            connectorType={node.connectorType as Parameters<typeof KnowledgeItemIcon>[0]['connectorType']}
            size={13}
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
