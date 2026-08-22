'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Flex, Text, Checkbox } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { ChatApi, type KnowledgeBaseForChat, type ListCollectionsForChatResult } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { useMainChatConnectorDefaultHint } from '@/chat/hooks/use-main-chat-connector-default-hint';
import { groupByTime, getNonEmptyGroups } from '@/lib/utils/group-by-time';
import { CollectionRow, CollectionLeadingIcon } from './collection-row';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import type { NodeType } from '@/app/(main)/knowledge-base/types';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';

// ── Types ──

/** Matches {@link ChatKnowledgeFilters} / API `filters` (`apps` + `kb`). */
export interface CollectionScopeSelection {
  apps: string[];
  kb: string[];
}

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

/**
 * A Collection/KB row (origin COLLECTION) each Collection is its own standalone `app` node with no nested
 * recordGroup layer underneath it — the row itself is the whole selectable
 * unit, so there's nothing to expand or fetch children for.
 */
function isCollectionAppRow(row: CollectionSelectItem): boolean {
  return (row.origin ?? '').toString().trim().toUpperCase() === 'COLLECTION';
}

const ROOT_APPS_PAGE_LIMIT = 20;
const MAX_ROOT_PAGES_RESTRICT_MODE = 200;

type ChildListMeta = {
  hasMore: boolean;
  nextPage: number;
  totalItems: number;
};

type HubPaginationSlice = {
  page?: number;
  hasNext?: boolean;
  totalItems?: number;
} | null | undefined;

/**
 * Shared hub pagination merge for the root apps list.
 */
function mergePagedListMeta(
  pagination: HubPaginationSlice,
  pageFetched: number,
  pageLimit: number,
  newItemsCount: number,
  previous: ChildListMeta | null
): ChildListMeta {
  const p = pagination;
  if (p && typeof p.hasNext === 'boolean') {
    const page = typeof p.page === 'number' ? p.page : pageFetched;
    const totalItems =
      typeof p.totalItems === 'number'
        ? p.totalItems
        : (previous?.totalItems ?? newItemsCount);
    return {
      hasMore: p.hasNext,
      nextPage: page + 1,
      totalItems,
    };
  }
  const cumulativeLoaded =
    previous != null ? previous.totalItems + newItemsCount : newItemsCount;
  return {
    hasMore: newItemsCount >= pageLimit,
    nextPage: pageFetched + 1,
    totalItems: cumulativeLoaded,
  };
}

function mergeRootsListMeta(
  res: ListCollectionsForChatResult,
  newRootsCount: number,
  previous: ChildListMeta | null
): ChildListMeta {
  return mergePagedListMeta(
    res.serverPagination,
    res.requestedPage,
    res.requestedLimit,
    newRootsCount,
    previous
  );
}

const CHECKBOX_ALIGN: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
  lineHeight: 0,
};

const OLIVE_ROW: React.CSSProperties = {
  width: '100%',
  minWidth: 0,
  boxSizing: 'border-box',
  height: 'var(--space-7)',
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-1)',
  paddingLeft: 'var(--space-3)',
  paddingRight: 'var(--space-2)',
  transition: 'background-color 0.15s',
};

// ── Component ──

/**
 * Control which subset of hub roots the tab displays.
 * - `'all'` (default): show everything.
 * - `'connectors'`: show only connector-app roots (not KB / COLLECTION-origin rows).
 * - `'collections'`: show only KB connector rows and COLLECTION-origin rows.
 */
export type CollectionsTabFilterMode = 'all' | 'connectors' | 'collections';

interface CollectionsTabProps {
  apps: string[];
  kb: string[];
  onSelectionChange: (next: CollectionScopeSelection) => void;
  /** When set, only these root ids are listed (e.g. agent-scoped collections). */
  restrictToKbIds?: string[] | null;
  /** Narrows which hub roots are shown — defaults to 'all'. */
  filterMode?: CollectionsTabFilterMode;
}

/**
 * Collections tab: time-grouped hub roots. Collection/KB rows (origin
 * COLLECTION) are standalone selectable units — no expand/children fetch.
 * Connector app rows (Slack, Drive, etc.) are likewise whole-app selectable.
 */
export function CollectionsTab({
  apps,
  kb,
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
        let page = 1;
        let prevMeta: ChildListMeta | null = null;
        let hasMore = true;
        let pagesFetched = 0;
        while (hasMore && pagesFetched < MAX_ROOT_PAGES_RESTRICT_MODE) {
          pagesFetched += 1;
          const res = await ChatApi.listCollectionsForChat({ page, limit: ROOT_APPS_PAGE_LIMIT });
          const batch = transformToCollectionItems(res.knowledgeBases);
          for (const c of batch) {
            if (seen.has(c.id)) continue;
            seen.add(c.id);
            mergedItems.push(c);
            if (allow.has(c.id)) foundAllowed.add(c.id);
          }
          prevMeta = mergeRootsListMeta(res, res.knowledgeBases.length, prevMeta);
          hasMore = prevMeta.hasMore;
          page = prevMeta.nextPage;
          if (foundAllowed.size >= allow.size) break;
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
        const res = await ChatApi.listCollectionsForChat({ page: 1, limit: ROOT_APPS_PAGE_LIMIT });
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
        page: rootsListMeta.nextPage,
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

  const toggleRoot = useCallback(
    (rootId: string) => {
      const row = collections.find((c) => c.id === rootId);
      const isCollection = row ? isCollectionAppRow(row) : false;

      if (isCollection) {
        if (kb.includes(rootId)) {
          onSelectionChange({ apps, kb: kb.filter((id) => id !== rootId) });
        } else {
          onSelectionChange({ apps, kb: [...kb, rootId] });
        }
      } else {
        // Connector app roots (Slack, Drive, etc.) — unchanged.
        if (apps.includes(rootId)) {
          onSelectionChange({ apps: apps.filter((x) => x !== rootId), kb });
        } else {
          onSelectionChange({ apps: [...apps, rootId], kb });
        }
      }
    },
    [apps, onSelectionChange, kb, collections]
  );

  const toggleRecordGroup = useCallback(
    (id: string) => {
      const nextKb = kb.includes(id) ? kb.filter((x) => x !== id) : [...kb, id];
      onSelectionChange({ apps, kb: nextKb });
    },
    [apps, onSelectionChange, kb]
  );

  // In 'collections' mode: flat list of Collection/KB root items — each is
  // already the whole selectable unit, nothing to expand or merge in.
  const flatKbItems = useMemo(() => {
    if (filterMode !== 'collections') return [];
    const query = searchQuery.toLowerCase();
    return collections
      .filter(isCollectionAppRow)
      .filter((row) => !query || row.name.toLowerCase().includes(query))
      .map((row) => ({ id: row.id, name: row.name }));
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

  /** Treat collection/record-group picks (`kb`) as scoped knowledge — same bar as connectors. */
  const showDefaultConnectorHint =
    mainChatConnectorDefaultHint &&
    apps.length === 0 &&
    kb.length === 0 &&
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
              {flatKbItems.map((kbItem) => (
                <CollectionRow
                  key={kbItem.id}
                  id={kbItem.id}
                  name={kbItem.name}
                  isSelected={kb.includes(kbItem.id)}
                  onToggle={() => toggleRecordGroup(kbItem.id)}
                />
              ))}
            </Flex>
          )
        )}

        {/* ── Normal mode (connectors / all): time-grouped root rows ── */}
        {filterMode !== 'collections' && !isLoading && !hasError && collections.length === 0 && (
          <Flex align="center" justify="center" style={{ padding: 'var(--space-4)' }}>
            <Text size="2" style={{ color: 'var(--slate-9)' }}>
              {searchQuery ? t('message.noCollections') : t('chat.noCollectionsAvailable')}
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

              {items.map((col) => {
                const isCollection = isCollectionAppRow(col);
                const rowChecked = isCollection ? kb.includes(col.id) : apps.includes(col.id);

                return (
                  <Flex key={col.id} direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
                    <Flex
                      align="center"
                      justify="between"
                      gap="1"
                      style={{
                        ...OLIVE_ROW,
                        cursor: 'default',
                      }}
                    >
                      <Flex
                        align="center"
                        gap="2"
                        style={{ minWidth: 0, flex: 1 }}
                        onClick={() => toggleRoot(col.id)}
                        role="presentation"
                      >
                        <span style={CHECKBOX_ALIGN}>
                          <Checkbox
                            size="1"
                            variant="classic"
                            checked={rowChecked}
                            onCheckedChange={() => toggleRoot(col.id)}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </span>
                        <span
                          style={{
                            display: 'inline-flex',
                            flexShrink: 0,
                            alignItems: 'center',
                            lineHeight: 0,
                          }}
                          aria-hidden
                        >
                          {isCollection ? (
                            <CollectionLeadingIcon
                              sourceType={col.connector?.trim() || col.subType?.trim() || 'KB'}
                              size={20}
                            />
                          ) : (
                            <KbNodeNameIcon
                              isKnowledgeHub
                              nodeType={col.nodeType as NodeType}
                              connector={col.connector?.trim() || col.subType?.trim() || null}
                              name={col.name}
                              size={20}
                            />
                          )}
                        </span>
                        <Text
                          size="2"
                          weight="medium"
                          truncate
                          style={{ color: 'var(--slate-11)', cursor: 'pointer' }}
                        >
                          {col.name}
                        </Text>
                      </Flex>
                    </Flex>
                  </Flex>
                );
              })}
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
            <button
              type="button"
              onClick={() => void loadMoreApps()}
              disabled={loadingMoreApps}
              style={{
                background: 'none',
                border: 'none',
                cursor: loadingMoreApps ? 'default' : 'pointer',
                color: 'var(--olive-9)',
                fontSize: 12,
                padding: 0,
                textAlign: 'left',
              }}
            >
              {loadingMoreApps ? t('agentBuilder.loadingMore') : t('agentBuilder.loadMore')}
            </button>
          </Flex>
        ) : null}
      </Flex>
    </Flex>
  );
}
