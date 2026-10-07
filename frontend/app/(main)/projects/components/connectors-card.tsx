'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Checkbox, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { CollectionLeadingIcon } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/collection-row';
import { ChatApi, type KnowledgeBaseForChat } from '@/chat/api';
import type { AppliedFilterNode, AppliedFilters } from '@/chat/types';
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

function toAppliedNode(row: ConnectorRow): AppliedFilterNode {
  return {
    id: row.id,
    name: row.name,
    nodeType: row.nodeType,
    connector: row.connector,
  };
}

interface ConnectorsCardProps {
  selectedAppIds: string[];
  knowledgeScopeKb: string[];
  appliedFiltersKb: AppliedFilterNode[];
  previousAppliedApps: AppliedFilterNode[];
  canEdit: boolean;
  onChange: (patch: { knowledgeScope: ProjectKnowledgeScope; appliedFilters: AppliedFilters }) => void;
}

/**
 * Project connectors picker — hub roots from
 * `GET /api/v1/knowledgeBase/knowledge-hub/nodes`, excluding Collection-origin
 * rows (those live under Files / `knowledgeScope.kb`). Selected ids persist as
 * `knowledgeScope.apps`, the allowlist `applyProjectScope` intersects with
 * each turn's `filters.apps`.
 */
export function ConnectorsCard({
  selectedAppIds,
  knowledgeScopeKb,
  appliedFiltersKb,
  previousAppliedApps,
  canEdit,
  onChange,
}: ConnectorsCardProps) {
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
        let page = 1;
        let hasMore = true;
        while (hasMore && page <= CONNECTORS_MAX_PAGES) {
          const res = await ChatApi.listCollectionsForChat({ page, limit: CONNECTORS_PAGE_LIMIT });
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
          hasMore =
            res.serverPagination?.hasNext === true ||
            (res.serverPagination == null && res.knowledgeBases.length >= CONNECTORS_PAGE_LIMIT);
          page += 1;
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

  const selectedSet = useMemo(() => new Set(selectedAppIds), [selectedAppIds]);
  const catalogById = useMemo(() => new Map(connectors.map((c) => [c.id, c])), [connectors]);
  const previousById = useMemo(
    () => new Map(previousAppliedApps.map((n) => [n.id, n])),
    [previousAppliedApps],
  );

  const persist = useCallback(
    (nextIds: string[]) => {
      const apps = nextIds.map((id) => {
        const row = catalogById.get(id);
        if (row) return toAppliedNode(row);
        return previousById.get(id) ?? { id, name: id, nodeType: 'app', connector: '' };
      });
      onChange({
        knowledgeScope: { apps: nextIds, kb: knowledgeScopeKb },
        appliedFilters: { apps, kb: appliedFiltersKb },
      });
    },
    [appliedFiltersKb, catalogById, knowledgeScopeKb, onChange, previousById],
  );

  const toggle = useCallback(
    (id: string, enabled: boolean) => {
      const next = new Set(selectedAppIds);
      if (enabled) next.add(id);
      else next.delete(id);
      persist(Array.from(next));
    },
    [persist, selectedAppIds],
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
  if (connectors.length === 0) {
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
      {connectors.map((row) => {
        const checked = selectedSet.has(row.id);
        return (
          <Flex
            key={row.id}
            align="center"
            gap="2"
            style={{
              padding: 'var(--space-2)',
              borderRadius: 'var(--radius-2)',
              background: 'var(--olive-1)',
              cursor: canEdit ? 'pointer' : 'default',
            }}
            onClick={() => canEdit && toggle(row.id, !checked)}
          >
            <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
              <Checkbox
                size="1"
                checked={checked}
                disabled={!canEdit}
                onCheckedChange={(v) => toggle(row.id, v === true)}
                onClick={(e) => e.stopPropagation()}
              />
            </span>
            <CollectionLeadingIcon sourceType={row.connector || row.nodeType} size={16} />
            <Text
              size="2"
              style={{
                color: 'var(--slate-12)',
                flex: 1,
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              truncate
            >
              {row.name}
            </Text>
          </Flex>
        );
      })}
    </Flex>
  );
}
