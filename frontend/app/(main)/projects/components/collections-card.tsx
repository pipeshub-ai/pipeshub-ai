'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { KnowledgeTreeRoot } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/knowledge-tree';
import { ChatApi, type KnowledgeBaseForChat } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import type { AppliedFilterNode, AppliedFilters, ChatKnowledgeFilters } from '@/chat/types';
import type { ProjectKnowledgeScope } from '@/chat/project-types';

const COLLECTIONS_PAGE_LIMIT = 100;

export interface CollectionRow {
  id: string;
  name: string;
  nodeType: string;
  connector: string;
}

function isCollectionItem(item: KnowledgeBaseForChat): boolean {
  const origin = (item.origin ?? '').toString().trim().toUpperCase();
  const connector = (item.connector ?? '').toString().trim().toUpperCase();
  return origin === 'COLLECTION' || connector === 'KB';
}

const BELOW_APPS = { recordGroups: 'recordGroup', records: 'folder' } as const;

export interface CollectionsCardProps {
  knowledgeScope: ProjectKnowledgeScope | undefined;
  appliedFilters: AppliedFilters | undefined;
  canEdit: boolean;
  onChange: (patch: { knowledgeScope: ProjectKnowledgeScope; appliedFilters: AppliedFilters }) => void;
}

/**
 * Project collections picker — hub roots from
 * `GET /api/v1/knowledgeBase/knowledge-hub/nodes` with `origins: 'COLLECTION'`.
 * A collection is ticked as a whole (`knowledgeScope.kb`) or opened to tick
 * folders and documents inside it (`knowledgeScope.recordGroups` / `.records`);
 * chats in the project search only what is ticked.
 */
export function CollectionsCard({ knowledgeScope, appliedFilters, canEdit, onChange }: CollectionsCardProps) {
  const { t } = useTranslation();
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setIsLoading(true);
      setLoadError(false);
      try {
        const seen = new Set<string>();
        const rows: CollectionRow[] = [];
        let cursor: string | null = null;
        while (true) {
          const res = await ChatApi.listCollectionsForChat({
            cursor,
            limit: COLLECTIONS_PAGE_LIMIT,
            origins: 'COLLECTION',
          });
          for (const item of res.knowledgeBases) {
            if (seen.has(item.id)) continue;
            if (!isCollectionItem(item)) continue;
            seen.add(item.id);
            rows.push({
              id: item.id,
              name:
                (item.name ?? '').trim() ||
                t('chat.projects.workspace.untitledCollection', { defaultValue: 'Untitled collection' }),
              nodeType: item.nodeType || 'app',
              connector: item.connector || 'KB',
            });
          }
          const nextCursor = res.nextCursor;
          if (!nextCursor || nextCursor === cursor) break;
          cursor = nextCursor;
        }
        if (!cancelled) setCollections(rows);
      } catch {
        if (!cancelled) setLoadError(true);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const catalogById = useMemo(() => new Map(collections.map((c) => [c.id, c])), [collections]);

  // Connectors are not picked here; the project's own connectors are kept as they are.
  const selection = useMemo<ChatKnowledgeFilters>(
    () => ({
      apps: [],
      kb: knowledgeScope?.kb ?? [],
      recordGroups: (knowledgeScope?.recordGroups ?? []).filter((id) => {
        const node = appliedFilters?.recordGroups?.find((n) => n.id === id);
        return node?.connector === 'KB';
      }),
      records: (knowledgeScope?.records ?? []).filter((id) => {
        const node = appliedFilters?.records?.find((n) => n.id === id);
        return node?.connector === 'KB';
      }),
    }),
    [knowledgeScope, appliedFilters],
  );

  /**
   * What the project already lists below app level for collections and this session
   * has not placed in a collection's tree yet: shown as rows of their own.
   * Connector-owned rows (connector !== 'KB') and rows whose ownership cannot be
   * determined are kept out so they are not shown as collection rows.
   */
  const savedNodes = useMemo(
    () =>
      (['recordGroups', 'records'] as const).flatMap((key) =>
        (knowledgeScope?.[key] ?? [])
          .filter((id) => !metaCache[id]?.ancestorIds)
          .map((id) => {
            const saved = appliedFilters?.[key]?.find((node) => node.id === id);
            return { id, saved };
          })
          .filter(({ saved }) => saved?.connector === 'KB')
          .map(({ id, saved }) => ({
            id,
            name: saved?.name ?? id,
            nodeType: saved?.nodeType || BELOW_APPS[key],
            connector: 'KB',
          })),
      ),
    [knowledgeScope, appliedFilters, metaCache],
  );

  const persist = useCallback(
    (next: ChatKnowledgeFilters) => {
      const picked = useChatStore.getState().collectionMetaCache;
      const saved = new Map(
        (['kb', 'recordGroups', 'records'] as const).flatMap((key) =>
          (appliedFilters?.[key] ?? []).map((node) => [node.id, node] as const),
        ),
      );
      const nodeOf = (id: string, fallbackType: string): AppliedFilterNode => {
        const row = catalogById.get(id) ?? picked[id];
        if (row) return { id, name: row.name, nodeType: row.nodeType, connector: row.connector || 'KB' };
        return saved.get(id) ?? { id, name: id, nodeType: fallbackType, connector: 'KB' };
      };

      // Preserve every ID that is not explicitly collection-owned (including scoped IDs with no node in appliedFilters).
      const isCollectionNode = (node: AppliedFilterNode | undefined) => node?.connector === 'KB';

      const preservedRecordGroups = (knowledgeScope?.recordGroups ?? []).filter((id) => {
        const node = appliedFilters?.recordGroups?.find((n) => n.id === id);
        return !isCollectionNode(node);
      });
      const preservedRecords = (knowledgeScope?.records ?? []).filter((id) => {
        const node = appliedFilters?.records?.find((n) => n.id === id);
        return !isCollectionNode(node);
      });

      const preservedFilterRecordGroups = (appliedFilters?.recordGroups ?? []).filter(
        (node) => !isCollectionNode(node),
      );
      const preservedFilterRecords = (appliedFilters?.records ?? []).filter(
        (node) => !isCollectionNode(node),
      );

      const collectionRecordGroups = next.recordGroups ?? [];
      const collectionRecords = next.records ?? [];

      const mergedRecordGroups = Array.from(new Set([...preservedRecordGroups, ...collectionRecordGroups]));
      const mergedRecords = Array.from(new Set([...preservedRecords, ...collectionRecords]));

      const collectionFilterRecordGroups = collectionRecordGroups.map((id) => nodeOf(id, BELOW_APPS.recordGroups));
      const collectionFilterRecords = collectionRecords.map((id) => nodeOf(id, BELOW_APPS.records));

      onChange({
        knowledgeScope: {
          apps: knowledgeScope?.apps ?? [],
          kb: next.kb ?? [],
          recordGroups: mergedRecordGroups,
          records: mergedRecords,
        },
        appliedFilters: {
          apps: appliedFilters?.apps ?? [],
          kb: (next.kb ?? []).map((id) => nodeOf(id, 'app')),
          recordGroups: [...preservedFilterRecordGroups, ...collectionFilterRecordGroups],
          records: [...preservedFilterRecords, ...collectionFilterRecords],
        },
      });
    },
    [appliedFilters, catalogById, knowledgeScope, onChange],
  );

  if (isLoading) {
    return (
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('common.loading', { defaultValue: 'Loading…' })}
      </Text>
    );
  }
  if (loadError) {
    return (
      <Text size="2" style={{ color: '#ef4444' }}>
        {t('chat.projects.workspace.failedToLoadCollections', {
          defaultValue: 'Failed to load collections',
        })}
      </Text>
    );
  }
  if (collections.length === 0 && savedNodes.length === 0) {
    return (
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('chat.projects.workspace.noCollectionsAvailable', {
          defaultValue: 'No collections to add yet.',
        })}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="1">
      <Text size="1" style={{ color: 'var(--slate-11)', marginBottom: 'var(--space-1)' }}>
        {t('chat.projects.workspace.collectionsSubtitle', {
          defaultValue: 'Chats in this project search the selected collections and uploaded project files.',
        })}
      </Text>
      <Flex
        direction="column"
        gap="1"
        style={{
          minHeight: 160,
          maxHeight: 360,
          overflow: 'auto',
          paddingBottom: 'var(--space-1)',
        }}
      >
        {savedNodes.map((node) => (
          <KnowledgeTreeRoot
            key={node.id}
            node={{ ...node, hasChildren: true, ancestorIds: [], bucket: 'kb' }}
            filters={selection}
            onSelectionChange={persist}
            readOnly={!canEdit}
          />
        ))}
        {collections.map((row) => (
          <KnowledgeTreeRoot
            key={row.id}
            node={{
              id: row.id,
              name: row.name,
              nodeType: 'app',
              connector: 'KB',
              bucket: 'kb',
              hasChildren: true,
              ancestorIds: [],
            }}
            filters={selection}
            onSelectionChange={persist}
            readOnly={!canEdit}
          />
        ))}
      </Flex>
    </Flex>
  );
}
