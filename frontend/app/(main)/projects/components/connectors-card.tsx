'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { KnowledgeTreeRoot } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/knowledge-tree';
import { ChatApi, type KnowledgeBaseForChat } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import type { AppliedFilterNode, AppliedFilters, ChatKnowledgeFilters } from '@/chat/types';
import type { ProjectKnowledgeScope } from '@/chat/project-types';

const CONNECTORS_PAGE_LIMIT = 100;
const CONNECTORS_MAX_PAGES = 10;

interface ConnectorRow {
  id: string;
  name: string;
  nodeType: string;
  connector: string;
}

function isCollectionOrigin(item: KnowledgeBaseForChat): boolean {
  return (item.origin ?? '').toString().trim().toUpperCase() === 'COLLECTION';
}

const BELOW_APPS = { recordGroups: 'recordGroup', records: 'folder' } as const;

interface ConnectorsCardProps {
  knowledgeScope: ProjectKnowledgeScope | undefined;
  appliedFilters: AppliedFilters | undefined;
  canEdit: boolean;
  onChange: (patch: { knowledgeScope: ProjectKnowledgeScope; appliedFilters: AppliedFilters }) => void;
}

/**
 * Project connectors picker — hub roots from
 * `GET /api/v1/knowledgeBase/knowledge-hub/nodes`, excluding Collection-origin
 * rows (those live under Files / `knowledgeScope.kb`). A connector is ticked
 * as a whole (`knowledgeScope.apps`) or opened to tick record groups, folders
 * and records inside it (`knowledgeScope.recordGroups` / `.records`); chats in
 * the project search only what is ticked.
 */
export function ConnectorsCard({ knowledgeScope, appliedFilters, canEdit, onChange }: ConnectorsCardProps) {
  const { t } = useTranslation();
  const [connectors, setConnectors] = useState<ConnectorRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setIsLoading(true);
      setLoadError(false);
      try {
        const seen = new Set<string>();
        const rows: ConnectorRow[] = [];
        // The listing pages by cursor, not by number: `nextCursor` is null at
        // the end of the result, and it is the only thing that resumes at the
        // right place — a page number names a position inside one ordering.
        let cursor: string | null = null;
        for (let page = 0; page < CONNECTORS_MAX_PAGES; page += 1) {
          const res = await ChatApi.listCollectionsForChat({
            cursor,
            limit: CONNECTORS_PAGE_LIMIT,
          });
          for (const item of res.knowledgeBases) {
            if (seen.has(item.id) || isCollectionOrigin(item)) continue;
            seen.add(item.id);
            rows.push({
              id: item.id,
              name: item.name,
              nodeType: item.nodeType || 'app',
              connector: item.connector ?? '',
            });
          }
          cursor = res.nextCursor;
          if (!cursor) break;
        }
        if (!cancelled) setConnectors(rows);
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
  }, []);

  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const catalogById = useMemo(() => new Map(connectors.map((c) => [c.id, c])), [connectors]);

  // Collections are not picked here; the project's own are kept as they are.
  const selection = useMemo<ChatKnowledgeFilters>(
    () => ({
      apps: knowledgeScope?.apps ?? [],
      kb: [],
      recordGroups: knowledgeScope?.recordGroups ?? [],
      records: knowledgeScope?.records ?? [],
    }),
    [knowledgeScope],
  );

  /**
   * What the project already lists below app level and this session has not
   * placed in a connector's tree yet: shown as rows of their own, so a saved
   * limit is visible without opening every connector.
   */
  const savedNodes = useMemo(
    () =>
      (['recordGroups', 'records'] as const).flatMap((key) =>
        (knowledgeScope?.[key] ?? [])
          .filter((id) => !metaCache[id]?.ancestorIds)
          .map((id) => {
            const saved = appliedFilters?.[key]?.find((node) => node.id === id);
            return {
              id,
              name: saved?.name ?? id,
              nodeType: saved?.nodeType || BELOW_APPS[key],
              connector: saved?.connector ?? '',
            };
          }),
      ),
    [knowledgeScope, appliedFilters, metaCache],
  );

  const persist = useCallback(
    (next: ChatKnowledgeFilters) => {
      const picked = useChatStore.getState().collectionMetaCache;
      const saved = new Map(
        (['apps', 'recordGroups', 'records'] as const).flatMap((key) =>
          (appliedFilters?.[key] ?? []).map((node) => [node.id, node] as const),
        ),
      );
      const nodeOf = (id: string, fallbackType: string): AppliedFilterNode => {
        const row = catalogById.get(id) ?? picked[id];
        if (row) return { id, name: row.name, nodeType: row.nodeType, connector: row.connector };
        return saved.get(id) ?? { id, name: id, nodeType: fallbackType, connector: '' };
      };
      const recordGroups = next.recordGroups ?? [];
      const records = next.records ?? [];
      onChange({
        knowledgeScope: { apps: next.apps, kb: knowledgeScope?.kb ?? [], recordGroups, records },
        appliedFilters: {
          apps: next.apps.map((id) => nodeOf(id, 'app')),
          kb: appliedFilters?.kb ?? [],
          recordGroups: recordGroups.map((id) => nodeOf(id, BELOW_APPS.recordGroups)),
          records: records.map((id) => nodeOf(id, BELOW_APPS.records)),
        },
      });
    },
    [appliedFilters, catalogById, knowledgeScope, onChange],
  );

  if (isLoading) {
    return (
      <Text size="2" style={{ color: 'var(--slate-10)' }}>
        {t('common.loading', { defaultValue: 'Loading…' })}
      </Text>
    );
  }
  if (loadError) {
    return (
      <Text size="2" style={{ color: '#ef4444' }}>
        {t('chat.projects.workspace.failedToLoadConnectors', {
          defaultValue: 'Failed to load connectors',
        })}
      </Text>
    );
  }
  if (connectors.length === 0 && savedNodes.length === 0) {
    return (
      <Text size="2" style={{ color: 'var(--slate-10)' }}>
        {t('chat.projects.workspace.noConnectorsAvailable', {
          defaultValue: 'No connectors to add yet.',
        })}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="1">
      <Text size="1" style={{ color: 'var(--slate-10)', marginBottom: 'var(--space-1)' }}>
        {t('chat.projects.workspace.connectorsSubtitle', {
          defaultValue: 'Chats in this project search only these connectors.',
        })}
      </Text>
      {savedNodes.map((node) => (
        <KnowledgeTreeRoot
          key={node.id}
          node={{ ...node, hasChildren: true, ancestorIds: [] }}
          filters={selection}
          onSelectionChange={persist}
          readOnly={!canEdit}
        />
      ))}
      {connectors.map((row) => (
        <KnowledgeTreeRoot
          key={row.id}
          node={{
            id: row.id,
            name: row.name,
            nodeType: 'app',
            connector: row.connector,
            hasChildren: true,
            ancestorIds: [],
          }}
          filters={selection}
          onSelectionChange={persist}
          readOnly={!canEdit}
        />
      ))}
    </Flex>
  );
}
