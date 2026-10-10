'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { ChatApi, type KnowledgeBaseForChat, type ListCollectionsForChatResult } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { hasAnyFilter } from '@/chat/utils/tree-selection';
import { useMainChatConnectorDefaultHint } from '@/chat/hooks/use-main-chat-connector-default-hint';
import { groupByTime, getNonEmptyGroups } from '@/lib/utils/group-by-time';
import { KnowledgeTreeRoot, LoadMoreLink, type KnowledgeTreeNode } from './knowledge-tree';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import type { ChatKnowledgeFilters } from '@/chat/types';

// ── Types ──

/** The picker's selection: the chat `filters`, down to record groups, folders and records. */
export type CollectionScopeSelection = ChatKnowledgeFilters;

export interface CollectionSelectItem {
  id: string;
  name: string;
  nodeType: string;
  updatedAt: number;
  hasChildren?: boolean;
  origin?: string;
  connector?: string;
  subType?: string;
}

// ── Transform API response ──

function transformToCollectionItems(
  knowledgeBases: KnowledgeBaseForChat[]
): CollectionSelectItem[] {
  return knowledgeBases.map((kb) => ({
    id: kb.id,
    name: kb.name,
    nodeType: kb.nodeType,
    updatedAt: kb.updatedAtTimestamp ?? kb.createdAtTimestamp ?? 0,
    hasChildren: kb.hasChildren,
    origin: kb.origin,
    connector: kb.connector,
    subType: kb.subType,
  }));
}

/** A Collection/KB row (origin COLLECTION): an `app` node whose children are its folders and files. */
function isCollectionAppRow(row: CollectionSelectItem): boolean {
  return (row.origin ?? '').toString().trim().toUpperCase() === 'COLLECTION';
}

/** A hub root as a row of the picker tree. */
function toTreeRoot(root: CollectionSelectItem): KnowledgeTreeNode {
  return {
    id: root.id,
    name: root.name,
    nodeType: 'app',
    connector: root.connector?.trim() || root.subType?.trim() || (isCollectionAppRow(root) ? 'KB' : ''),
    hasChildren: root.hasChildren,
    ancestorIds: [],
  };
}

const ROOT_APPS_PAGE_LIMIT = 20;
const MAX_ROOT_PAGES_RESTRICT_MODE = 200;

type ChildListMeta = {
  hasMore: boolean;
  nextCursor: string | null;
  totalItems: number;
};

/**
 * Fold one root-apps page into the running list meta.
 *
 * Keyset paging ends at the absent cursor rather than at a page count, so
 * "there is more" is exactly "the server issued a next cursor". `totalItems`
 * falls back to what has been loaded when the server reports no total.
 */
function mergeRootsListMeta(
  res: ListCollectionsForChatResult,
  newRootsCount: number,
  previous: ChildListMeta | null
): ChildListMeta {
  const totalItems =
    typeof res.serverPagination?.totalItems === 'number'
      ? res.serverPagination.totalItems
      : (previous?.totalItems ?? 0) + newRootsCount;
  return {
    hasMore: Boolean(res.nextCursor),
    nextCursor: res.nextCursor,
    totalItems,
  };
}

// ── Component ──

/**
 * Control which subset of hub roots the tab displays.
 * - `'all'` (default): show everything.
 * - `'connectors'`: show only connector-app roots (not KB / COLLECTION-origin rows).
 * - `'collections'`: show only KB connector rows and COLLECTION-origin rows.
 */
export type CollectionsTabFilterMode = 'all' | 'connectors' | 'collections';

interface CollectionsTabProps {
  selection: CollectionScopeSelection;
  onSelectionChange: (next: CollectionScopeSelection) => void;
  /** When set, only these root ids are listed (e.g. agent-scoped collections). */
  restrictToKbIds?: string[] | null;
  /** Narrows which hub roots are shown — defaults to 'all'. */
  filterMode?: CollectionsTabFilterMode;
}

/**
 * Collections tab: time-grouped hub roots (connector apps and collections).
 * Each root can be selected as a whole, or expanded to select record groups,
 * folders and records inside it.
 */
export function CollectionsTab({
  selection,
  onSelectionChange,
  restrictToKbIds = null,
  filterMode = 'all',
}: CollectionsTabProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchFocused, setSearchFocused] = useState(false);
  const [collections, setCollections] = useState<CollectionSelectItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [rootsListMeta, setRootsListMeta] = useState<ChildListMeta | null>(null);
  const [loadingMoreApps, setLoadingMoreApps] = useState(false);

  const setCollectionNamesCache = useChatStore((s) => s.setCollectionNamesCache);
  const setCollectionMetaCache = useChatStore((s) => s.setCollectionMetaCache);
  const { t } = useTranslation();
  const mainChatConnectorDefaultHint = useMainChatConnectorDefaultHint();

  const fetchCollections = useCallback(async () => {
    try {
      setIsLoading(true);
      setHasError(false);
      setRootsListMeta(null);
      setLoadingMoreApps(false);

      const restrict = Boolean(restrictToKbIds && restrictToKbIds.length > 0);

      if (restrict && restrictToKbIds) {
        const allow = new Set(restrictToKbIds);
        const seen = new Set<string>();
        /** Ids from `allow` seen in fetched pages — stop paging once all are found. */
        const foundAllowed = new Set<string>();
        const mergedItems: CollectionSelectItem[] = [];
        let cursor: string | null = null;
        const seenCursors = new Set<string>();
        let prevMeta: ChildListMeta | null = null;
        let hasMore = true;
        let pagesFetched = 0;
        while (hasMore && pagesFetched < MAX_ROOT_PAGES_RESTRICT_MODE) {
          pagesFetched += 1;
          const res = await ChatApi.listCollectionsForChat({ cursor, limit: ROOT_APPS_PAGE_LIMIT });
          const batch = transformToCollectionItems(res.knowledgeBases);
          for (const c of batch) {
            if (seen.has(c.id)) continue;
            seen.add(c.id);
            mergedItems.push(c);
            if (allow.has(c.id)) foundAllowed.add(c.id);
          }
          prevMeta = mergeRootsListMeta(res, res.knowledgeBases.length, prevMeta);
          hasMore = prevMeta.hasMore;
          cursor = prevMeta.nextCursor;
          if (foundAllowed.size >= allow.size) break;
          // A server echoing a cursor back instead of advancing would spin here.
          if (cursor) {
            if (seenCursors.has(cursor)) break;
            seenCursors.add(cursor);
          }
        }
        const items = mergedItems.filter((c) => allow.has(c.id));
        setCollections(items);
        setRootsListMeta(null);
        const nameMap: Record<string, string> = {};
        const metaMap: Record<string, { name: string; nodeType: string; connector: string }> = {};
        items.forEach((item) => {
          nameMap[item.id] = item.name;
          metaMap[item.id] = { name: item.name, nodeType: item.nodeType, connector: item.connector ?? '' };
        });
        setCollectionNamesCache(nameMap);
        setCollectionMetaCache(metaMap);
      } else {
        const res = await ChatApi.listCollectionsForChat({ limit: ROOT_APPS_PAGE_LIMIT });
        const items = transformToCollectionItems(res.knowledgeBases);
        setCollections(items);
        setRootsListMeta(mergeRootsListMeta(res, res.knowledgeBases.length, null));
        const nameMap: Record<string, string> = {};
        const metaMap: Record<string, { name: string; nodeType: string; connector: string }> = {};
        items.forEach((item) => {
          nameMap[item.id] = item.name;
          metaMap[item.id] = { name: item.name, nodeType: item.nodeType, connector: item.connector ?? '' };
        });
        setCollectionNamesCache(nameMap);
        setCollectionMetaCache(metaMap);
      }
    } catch (err) {
      setHasError(true);
      console.error('Error fetching collections for chat:', err);
    } finally {
      setIsLoading(false);
    }
  }, [restrictToKbIds, setCollectionNamesCache, setCollectionMetaCache]);

  const loadMoreApps = useCallback(async () => {
    if (!rootsListMeta?.hasMore || loadingMoreApps) return;
    setLoadingMoreApps(true);
    try {
      const res = await ChatApi.listCollectionsForChat({
        cursor: rootsListMeta.nextCursor,
        limit: ROOT_APPS_PAGE_LIMIT,
      });
      const newItems = transformToCollectionItems(res.knowledgeBases);
      setCollections((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        const merged = [...prev];
        for (const c of newItems) {
          if (!seen.has(c.id)) {
            seen.add(c.id);
            merged.push(c);
          }
        }
        return merged;
      });
      setRootsListMeta((prev) => mergeRootsListMeta(res, res.knowledgeBases.length, prev));
      if (newItems.length > 0) {
        const nameMap: Record<string, string> = {};
        const metaMap: Record<string, { name: string; nodeType: string; connector: string }> = {};
        newItems.forEach((item) => {
          nameMap[item.id] = item.name;
          metaMap[item.id] = { name: item.name, nodeType: item.nodeType, connector: item.connector ?? '' };
        });
        setCollectionNamesCache(nameMap);
        setCollectionMetaCache(metaMap);
      }
    } catch (err) {
      setHasError(true);
      console.error('Error loading more hub apps for chat:', err);
    } finally {
      setLoadingMoreApps(false);
    }
  }, [rootsListMeta, loadingMoreApps, setCollectionNamesCache, setCollectionMetaCache]);

  useEffect(() => {
    fetchCollections();
  }, [fetchCollections]);

  // In 'collections' mode: the Collection/KB roots as one flat list.
  const flatKbItems = useMemo(() => {
    if (filterMode !== 'collections') return [];
    const query = searchQuery.toLowerCase();
    return collections
      .filter(isCollectionAppRow)
      .filter((row) => !query || row.name.toLowerCase().includes(query));
  }, [filterMode, collections, searchQuery]);

  const groupedCollections = useMemo(() => {
    // In 'collections' mode we render flatKbItems instead of this grouped list
    if (filterMode === 'collections') return [];

    let filtered = searchQuery
      ? collections.filter((c) =>
          c.name.toLowerCase().includes(searchQuery.toLowerCase())
        )
      : collections;

    // 'connectors' mode: show only non-Collection connector app roots
    if (filterMode === 'connectors') {
      filtered = filtered.filter((c) => !isCollectionAppRow(c));
    }

    const groups = groupByTime(filtered, (c) => c.updatedAt);
    return getNonEmptyGroups(groups, (c) => c.updatedAt);
  }, [collections, searchQuery, filterMode]);

  const showDefaultConnectorHint =
    mainChatConnectorDefaultHint &&
    !hasAnyFilter(selection) &&
    (filterMode === 'all' || filterMode === 'connectors');

  return (
    <Flex
      direction="column"
      gap="2"
      style={{ flex: 1, minWidth: 0, width: '100%', overflow: 'hidden' }}
    >
      <Flex align="center" gap="1" style={{ width: '100%' }}>
        <Flex
          align="center"
          gap="2"
          style={{
            flex: 1,
            height: 'var(--space-6)',
            border: `1px solid ${searchFocused ? 'var(--accent-10)' : 'var(--slate-a5)'}`,
            borderRadius: 'var(--radius-2)',
            paddingLeft: 'var(--space-2)',
            paddingRight: 'var(--space-2)',
            backgroundColor: 'var(--slate-1)',
          }}
        >
          <MaterialIcon
            name="search"
            size={ICON_SIZES.PRIMARY}
            color="var(--slate-9)"
          />
          <input
            type="text"
            className="collections-search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onFocus={() => setSearchFocused(true)}
            onBlur={() => setSearchFocused(false)}
            placeholder={t('chat.searchCollections')}
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              color: 'var(--slate-12)',
              fontSize: 'var(--font-size-2)',
              fontFamily: 'inherit',
            }}
          />
        </Flex>
      </Flex>

      {showDefaultConnectorHint && (
        <Text
          size="1"
          style={{
            color: 'var(--slate-11)',
            padding: '0 var(--space-1)',
            flexShrink: 0,
          }}
        >
          {t('chat.connectorsDefaultScopeHint')}
        </Text>
      )}

      <Flex
        direction="column"
        gap="1"
        className="no-scrollbar"
        style={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          width: '100%',
          overflowY: 'auto',
        }}
      >
        {isLoading && (
          <Flex align="center" justify="center" style={{ flex: 1, minHeight: 0 }}>
            <LottieLoader variant="loader" size={48} />
          </Flex>
        )}

        {hasError && !isLoading && (
          <Flex align="center" justify="center" style={{ padding: 'var(--space-4)' }}>
            <Text size="2" style={{ color: 'var(--red-9)' }}>
              {t('chat.failedToLoadCollections')}
            </Text>
          </Flex>
        )}

        {/* ── Collections mode: flat KB items, no parent container row ── */}
        {filterMode === 'collections' && !isLoading && !hasError && (
          flatKbItems.length === 0 ? (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-4)' }}>
              <Text size="2" style={{ color: 'var(--slate-9)' }}>
                {searchQuery ? t('message.noCollections') : t('chat.noCollectionsAvailable')}
              </Text>
            </Flex>
          ) : (
            <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
              {flatKbItems.map((root) => (
                <KnowledgeTreeRoot key={root.id} node={toTreeRoot(root)} filters={selection} onSelectionChange={onSelectionChange} />
              ))}
            </Flex>
          )
        )}

        {/* ── Normal mode (connectors / all): time-grouped root rows ── */}
        {filterMode !== 'collections' && !isLoading && !hasError && groupedCollections.length === 0 && (
          <Flex align="center" justify="center" style={{ padding: 'var(--space-4)' }}>
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {searchQuery || collections.length > 0
                ? t(filterMode === 'connectors' ? 'chat.noConnectorsFound' : 'message.noCollections')
                : t('chat.noCollectionsAvailable')}
            </Text>
          </Flex>
        )}

        {filterMode !== 'collections' &&
          !isLoading &&
          !hasError &&
          groupedCollections.map(([label, items]) => (
            <Flex key={label} direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
              <Flex
                align="center"
                style={{
                  height: '28px',
                  paddingLeft: 'var(--space-1)',
                }}
              >
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 400,
                    lineHeight: 'var(--line-height-1)',
                    letterSpacing: '0.04px',
                    color: 'var(--olive-9)',
                  }}
                >
                  {label}
                </span>
              </Flex>

              {items.map((root) => (
                <KnowledgeTreeRoot key={root.id} node={toTreeRoot(root)} filters={selection} onSelectionChange={onSelectionChange} />
              ))}
            </Flex>
          ))}

        {!isLoading && !hasError && rootsListMeta?.hasMore && !restrictToKbIds?.length ? (
          <Flex
            align="center"
            style={{
              width: '100%',
              minWidth: 0,
              paddingTop: 'var(--space-2)',
              boxSizing: 'border-box',
            }}
          >
            <LoadMoreLink loading={loadingMoreApps} onClick={() => void loadMoreApps()} />
          </Flex>
        ) : null}
      </Flex>
    </Flex>
  );
}
