'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useReactFlow, type Node } from '@xyflow/react';
import { Button, Dialog, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KnowledgeTreeRoot } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/knowledge-tree';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters } from '@/chat/types';
import { hasAnyFilter, sameSelection } from '@/chat/utils/tree-selection';
import type { AgentKnowledgeNode } from '@/app/(main)/agents/api';
import type { FlowNodeData } from '../types';

/** What a knowledge source is limited to, as saved in its `filters`. */
export interface KnowledgeSourceLimit {
  recordGroups: string[];
  records: string[];
  /** Names of the nodes above, for the screens that list them. */
  nodes: AgentKnowledgeNode[];
}

/** The id a knowledge node's source is saved under, or '' for a node that is not one source. */
export function knowledgeSourceId(data: FlowNodeData): string {
  if (data.type === 'kb-group' || data.type === 'app-group') return '';
  if (data.type.startsWith('kb-')) return (data.config.kbId as string) || '';
  if (data.type.startsWith('app-')) return (data.config.connectorInstanceId as string) || (data.config.id as string) || '';
  return '';
}

export function limitOf(data: FlowNodeData): KnowledgeSourceLimit {
  const filters = (data.config.filters as Record<string, unknown>) ?? {};
  const ids = (selected: unknown, stored: unknown): string[] =>
    (Array.isArray(selected) ? selected : Array.isArray(stored) ? stored : []).filter(
      (id): id is string => typeof id === 'string'
    );
  return {
    recordGroups: ids(data.config.selectedRecordGroups, filters.recordGroups),
    records: ids(data.config.selectedRecords, filters.records),
    nodes: Array.isArray(filters.nodes) ? (filters.nodes as AgentKnowledgeNode[]) : [],
  };
}

/** The picker's selection for a source: the whole source, or the nodes it is limited to. */
export function selectionOf(sourceId: string, limit: KnowledgeSourceLimit): ChatKnowledgeFilters {
  const limited = limit.recordGroups.length > 0 || limit.records.length > 0;
  return { apps: limited ? [] : [sourceId], kb: [], recordGroups: limit.recordGroups, records: limit.records };
}

/** The limit a selection stands for; the whole source is no limit at all. */
export function limitFromSelection(
  sourceId: string,
  selection: ChatKnowledgeFilters,
  nameOf: (id: string) => AgentKnowledgeNode | undefined
): KnowledgeSourceLimit {
  if (selection.apps.includes(sourceId)) return { recordGroups: [], records: [], nodes: [] };
  const recordGroups = selection.recordGroups ?? [];
  const records = selection.records ?? [];
  return {
    recordGroups,
    records,
    nodes: [...recordGroups, ...records].flatMap((id) => nameOf(id) ?? []),
  };
}

/** Whether two limits list the same record groups and records, in any order. */
export function sameLimit(a: KnowledgeSourceLimit, b: KnowledgeSourceLimit): boolean {
  return sameSelection(
    { apps: [], kb: [], recordGroups: a.recordGroups, records: a.records },
    { apps: [], kb: [], recordGroups: b.recordGroups, records: b.records }
  );
}

/** The node's config with the limit written where saving an agent reads it. */
export function withLimit(config: Record<string, unknown>, limit: KnowledgeSourceLimit): Record<string, unknown> {
  return {
    ...config,
    selectedRecordGroups: limit.recordGroups,
    selectedRecords: limit.records,
    filters: { ...((config.filters as Record<string, unknown>) ?? {}), ...limit },
  };
}

/**
 * On a knowledge node: opens a picker to limit what the agent searches in that
 * source to some of its record groups, folders and records.
 */
export function KnowledgeSourceLimitButton({ nodeId, data }: { nodeId: string; data: FlowNodeData }) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow<Node<FlowNodeData>>();
  const sourceId = knowledgeSourceId(data);
  const saved = limitOf(data);
  const [draft, setDraft] = useState<ChatKnowledgeFilters | null>(null);
  if (!sourceId) return null;

  const title = t('agentBuilder.limitSource', { name: data.label });
  const save = () => {
    if (!draft) return;
    const picked = useChatStore.getState().collectionMetaCache;
    const known = new Map(saved.nodes.map((node) => [node.id, node]));
    const limit = limitFromSelection(sourceId, draft, (id) =>
      picked[id] ? { id, name: picked[id].name, nodeType: picked[id].nodeType } : known.get(id)
    );
    // Saving what is already saved must not mark the agent as changed.
    if (!sameLimit(limit, saved)) {
      setNodes((nodes) =>
        nodes.map((node) =>
          node.id === nodeId ? { ...node, data: { ...node.data, config: withLimit(node.data.config, limit) } } : node
        )
      );
    }
    setDraft(null);
  };

  return (
    <>
      <Tooltip content={title}>
        <IconButton
          size="1"
          variant="ghost"
          color="gray"
          onClick={() => setDraft(selectionOf(sourceId, saved))}
          aria-label={title}
        >
          <MaterialIcon name="filter_list" size={18} color="var(--agent-flow-text)" />
        </IconButton>
      </Tooltip>
      <Dialog.Root open={draft !== null} onOpenChange={(open) => !open && setDraft(null)}>
        <Dialog.Content style={{ maxWidth: 520 }}>
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description size="2" mb="3" style={{ color: 'var(--slate-11)' }}>
            {t('agentBuilder.limitSourceHint')}
          </Dialog.Description>
          {draft ? (
            <Flex direction="column" style={{ maxHeight: '50vh', overflowY: 'auto' }}>
              <KnowledgeTreeRoot
                node={{
                  id: sourceId,
                  name: data.label,
                  nodeType: 'app',
                  connector: data.type.startsWith('kb-') ? 'KB' : ((data.config.connectorType as string) ?? ''),
                  hasChildren: true,
                  ancestorIds: [],
                }}
                filters={draft}
                onSelectionChange={setDraft}
              />
            </Flex>
          ) : null}
          <Flex justify="end" gap="2" mt="4">
            <Button variant="soft" color="gray" onClick={() => setDraft(null)}>
              {t('action.cancel')}
            </Button>
            <Button onClick={save} disabled={!hasAnyFilter(draft)}>
              {t('action.save')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}

/** "Limited to N selected", or nothing for a source used as a whole. */
export function KnowledgeSourceLimitNote({ data }: { data: FlowNodeData }) {
  const { t } = useTranslation();
  const limit = limitOf(data);
  const count = limit.recordGroups.length + limit.records.length;
  if (!knowledgeSourceId(data) || count === 0) return null;
  return (
    <Text size="1" style={{ display: 'block', color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}>
      {t('agentBuilder.limitSourceCount', { count })}
    </Text>
  );
}
