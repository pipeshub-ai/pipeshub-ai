'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex } from '@radix-ui/themes';
import { KnowledgeTreeRoot } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/knowledge-tree';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters } from '@/chat/types';
import { hasAnyFilter, sameSelection } from '@/chat/utils/tree-selection';
import type { AgentKnowledgeNode } from '@/app/(main)/agents/api';

/** What a knowledge source is limited to, as saved in its `filters`. */
export interface KnowledgeSourceLimit {
  recordGroups: string[];
  records: string[];
  /** Names of the nodes above, for the screens that list them. */
  nodes: AgentKnowledgeNode[];
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

/** The source a limit is picked in: a Collection (`KB`) or a connector, with its name. */
export interface KnowledgeLimitSource {
  id: string;
  name: string;
  /** `KB` for a Collection, else the connector's type. */
  connector: string;
}

/**
 * The picker that limits what an agent searches in one source to some of its record groups, folders and records.
 * The agent builder's knowledge node and the chat's agent draft both open it.
 */
export function KnowledgeSourceLimitDialog({
  source,
  saved,
  onSave,
  onClose,
}: {
  /** The source to pick in; null while the picker is closed. */
  source: KnowledgeLimitSource | null;
  saved: KnowledgeSourceLimit;
  /** The limit picked; called only when it differs from `saved`. */
  onSave: (limit: KnowledgeSourceLimit) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<ChatKnowledgeFilters | null>(null);
  const [openFor, setOpenFor] = useState<string | null>(null);
  // Opening on a source starts from what is saved for it.
  if (source && openFor !== source.id) {
    setOpenFor(source.id);
    setDraft(selectionOf(source.id, saved));
  } else if (!source && openFor !== null) {
    setOpenFor(null);
    setDraft(null);
  }

  const title = source ? t('agentBuilder.limitSource', { name: source.name }) : '';
  const save = () => {
    if (!source || !draft) return;
    const picked = useChatStore.getState().collectionMetaCache;
    const known = new Map(saved.nodes.map((node) => [node.id, node]));
    const limit = limitFromSelection(source.id, draft, (id) =>
      picked[id] ? { id, name: picked[id].name, nodeType: picked[id].nodeType } : known.get(id)
    );
    // Saving what is already saved must not mark anything as changed.
    if (!sameLimit(limit, saved)) onSave(limit);
    onClose();
  };

  return (
    <Dialog.Root open={source !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Content style={{ maxWidth: 520 }}>
        <Dialog.Title>{title}</Dialog.Title>
        <Dialog.Description size="2" mb="3" style={{ color: 'var(--text-secondary)' }}>
          {t('agentBuilder.limitSourceHint')}
        </Dialog.Description>
        {source && draft ? (
          <Flex direction="column" style={{ maxHeight: '50vh', overflowY: 'auto' }}>
            <KnowledgeTreeRoot
              node={{
                id: source.id,
                name: source.name,
                nodeType: 'app',
                connector: source.connector,
                hasChildren: true,
                ancestorIds: [],
              }}
              filters={draft}
              onSelectionChange={setDraft}
            />
          </Flex>
        ) : null}
        <Flex justify="end" gap="2" mt="4">
          <Button variant="outline" color="gray" onClick={onClose}>
            {t('action.cancel')}
          </Button>
          <Button onClick={save} disabled={!draft || !hasAnyFilter(draft)}>
            {t('action.save')}
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
