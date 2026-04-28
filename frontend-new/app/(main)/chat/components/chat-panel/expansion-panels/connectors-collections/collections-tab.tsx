'use client';

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Flex, Text, Checkbox, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { ChatApi, type KnowledgeBaseForChat, type ListCollectionsForChatResult } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { groupByTime, getNonEmptyGroups } from '@/lib/utils/group-by-time';
import { CollectionRow, CollectionLeadingIcon } from './collection-row';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import type { KnowledgeHubApiResponse, KnowledgeHubNode, NodeType } from '@/app/(main)/knowledge-base/types';
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

/** KB / “Collections” connector hub row (expand to list KB record groups). */
function isKbCollectionsConnectorRow(row: CollectionSelectItem): boolean {
  const c = (row.connector ?? '').toString().trim().toUpperCase();
  if (c === 'KB') return true;
  const st = (row.subType ?? '').toString().trim().toUpperCase();
  return st === 'KB';
}

/** COLLECTION-origin container that can hold nested collection / record-group children. */
function isCollectionScopeExpandableRow(row: CollectionSelectItem): boolean {
  if ((row.origin ?? '').toString().toUpperCase() !== 'COLLECTION') return false;
  return row.nodeType === 'app' || row.nodeType === 'kb' || row.nodeType === 'recordGroup';
}

const CHILD_PAGE_LIMIT = 20;
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
 * Shared hub pagination merge (record-group children under a root, or hub root apps list).
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

function mapRecordGroupItems(items: KnowledgeHubNode[]): { id: string; name: string }[] {
  return items
    .filter((item) => item.nodeType === 'recordGroup')
    .map((item) => ({ id: item.id, name: item.name }));
}

/**
 * Derive next-page cursor after a hub `getDataNodes` response.
 * If the API omits `pagination`, infer `hasMore` when a full page of rows was returned.
 */
function mergeChildListMeta(
  data: KnowledgeHubApiResponse,
  pageFetched: number,
  newKidsCount: number,
  previous?: ChildListMeta | null
): ChildListMeta {
  return mergePagedListMeta(data.pagination, pageFetched, CHILD_PAGE_LIMIT, newKidsCount, previous ?? null);
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
 * Collections tab: time-grouped hub roots. Chevron only on KB connector + COLLECTION
 * containers (not other connector apps). Expand loads record groups under that parent.
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
  const [expandedRootIds, setExpandedRootIds] = useState<Record<string, boolean>>({});
  const [childrenByRootId, setChildrenByRootId] = useState<
    Record<string, { id: string; name: string }[]>
  >({});
  const [childrenErrorByRootId, setChildrenErrorByRootId] = useState<Record<string, string>>({});
  const [childrenMetaByRootId, setChildrenMetaByRootId] = useState<Record<string, ChildListMeta>>({});
  // Set so multiple containers can show their individual spinners concurrently
  const [loadingChildrenRootIds, setLoadingChildrenRootIds] = useState<Set<string>>(new Set());
  const [loadingMoreRootId, setLoadingMoreRootId] = useState<string | null>(null);
  const [rootsListMeta, setRootsListMeta] = useState<ChildListMeta | null>(null);
  const [loadingMoreApps, setLoadingMoreApps] = useState(false);
  const loadedChildrenRef = useRef<Set<string>>(new Set());
  // Tracks every in-flight loadChildren call; prevents duplicate concurrent fetches per ID
  const loadingChildRootRef = useRef<Set<string>>(new Set());

  const setCollectionNamesCache = useChatStore((s) => s.setCollectionNamesCache);
  const { t } = useTranslation();

  const fetchCollections = useCallback(async () => {
    try {
      setIsLoading(true);
      setHasError(false);
      loadedChildrenRef.current.clear();
      loadingChildRootRef.current.clear();
      setChildrenByRootId({});
      setChildrenMetaByRootId({});
      setExpandedRootIds({});
      setChildrenErrorByRootId({});
      setLoadingChildrenRootIds(new Set());
      setLoadingMoreRootId(null);
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
        items.forEach((item) => {
          nameMap[item.id] = item.name;
        });
        setCollectionNamesCache(nameMap);
      } else {
        const res = await ChatApi.listCollectionsForChat({ page: 1, limit: ROOT_APPS_PAGE_LIMIT });
        const items = transformToCollectionItems(res.knowledgeBases);
        setCollections(items);
        setRootsListMeta(mergeRootsListMeta(res, res.knowledgeBases.length, null));
        const nameMap: Record<string, string> = {};
        items.forEach((item) => {
          nameMap[item.id] = item.name;
        });
        setCollectionNamesCache(nameMap);
      }
    } catch (err) {
      setHasError(true);
      console.error('Error fetching collections for chat:', err);
    } finally {
      setIsLoading(false);
    }
  }, [restrictToKbIds, setCollectionNamesCache]);

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
        newItems.forEach((item) => {
          nameMap[item.id] = item.name;
        });
        setCollectionNamesCache(nameMap);
      }
    } catch (err) {
      setHasError(true);
      console.error('Error loading more hub apps for chat:', err);
    } finally {
      setLoadingMoreApps(false);
    }
  }, [rootsListMeta, loadingMoreApps, setCollectionNamesCache]);

  useEffect(() => {
    fetchCollections();
  }, [fetchCollections]);

  const showExpandChevron = useCallback((row: CollectionSelectItem) => {
    if (row.hasChildren === false) return false;
    return isKbCollectionsConnectorRow(row) || isCollectionScopeExpandableRow(row);
  }, []);

  const loadChildren = useCallback(async (row: CollectionSelectItem) => {
    if (loadedChildrenRef.current.has(row.id) || loadingChildRootRef.current.has(row.id)) return;
    loadingChildRootRef.current.add(row.id);
    setLoadingChildrenRootIds((prev) => new Set(prev).add(row.id));
    setChildrenErrorByRootId((prev) => {
      const next = { ...prev };
      delete next[row.id];
      return next;
    });
    setChildrenMetaByRootId((prev) => {
      const next = { ...prev };
      delete next[row.id];
      return next;
    });
    try {
      const data = await KnowledgeHubApi.getDataNodes(row.nodeType as NodeType, row.id, {
        page: 1,
        limit: CHILD_PAGE_LIMIT,
        sortBy: 'name',
        sortOrder: 'asc',
        nodeTypes: 'recordGroup',
        onlyContainers: false,
      });
      const kids = mapRecordGroupItems(data.items);
      setChildrenByRootId((prev) => ({ ...prev, [row.id]: kids }));
      setChildrenMetaByRootId((prev) => ({
        ...prev,
        [row.id]: mergeChildListMeta(data, 1, kids.length, null),
      }));
      loadedChildrenRef.current.add(row.id);
      if (kids.length > 0) {
        const kidNames: Record<string, string> = {};
        for (const k of kids) kidNames[k.id] = k.name;
        setCollectionNamesCache(kidNames);
      }
    } catch {
      setChildrenErrorByRootId((prev) => ({
        ...prev,
        [row.id]: t('chat.failedToLoadCollections', { defaultValue: 'Failed to load' }),
      }));
    } finally {
      loadingChildRootRef.current.delete(row.id);
      setLoadingChildrenRootIds((prev) => {
        const next = new Set(prev);
        next.delete(row.id);
        return next;
      });
    }
  }, [setCollectionNamesCache, t]);

  // When in 'collections' mode, auto-fetch KB children immediately on root list change
  useEffect(() => {
    if (filterMode !== 'collections') return;
    for (const row of collections) {
      if (isKbCollectionsConnectorRow(row) || isCollectionScopeExpandableRow(row)) {
        void loadChildren(row);
      }
    }
  }, [collections, filterMode, loadChildren]);

  const loadMoreChildren = useCallback(
    async (row: CollectionSelectItem) => {
      const meta = childrenMetaByRootId[row.id];
      if (!meta?.hasMore) return;
      if (loadingMoreRootId === row.id || loadingChildrenRootIds.has(row.id)) return;
      setLoadingMoreRootId(row.id);
      setChildrenErrorByRootId((prev) => {
        const next = { ...prev };
        delete next[row.id];
        return next;
      });
      try {
        const data = await KnowledgeHubApi.getDataNodes(row.nodeType as NodeType, row.id, {
          page: meta.nextPage,
          limit: CHILD_PAGE_LIMIT,
          sortBy: 'name',
          sortOrder: 'asc',
          nodeTypes: 'recordGroup',
          onlyContainers: false,
        });
        const newKids = mapRecordGroupItems(data.items);
        setChildrenByRootId((prev) => {
          const existing = prev[row.id] ?? [];
          const seen = new Set(existing.map((k) => k.id));
          const merged = [...existing];
          for (const k of newKids) {
            if (!seen.has(k.id)) {
              seen.add(k.id);
              merged.push(k);
            }
          }
          return { ...prev, [row.id]: merged };
        });
        setChildrenMetaByRootId((prev) => ({
          ...prev,
          [row.id]: mergeChildListMeta(data, meta.nextPage, newKids.length, meta),
        }));
        if (newKids.length > 0) {
          const kidNames: Record<string, string> = {};
          for (const k of newKids) kidNames[k.id] = k.name;
          setCollectionNamesCache(kidNames);
        }
      } catch {
        setChildrenErrorByRootId((prev) => ({
          ...prev,
          [row.id]: t('chat.failedToLoadCollections', { defaultValue: 'Failed to load' }),
        }));
      } finally {
        setLoadingMoreRootId(null);
      }
    },
    [childrenMetaByRootId, loadingChildrenRootIds, loadingMoreRootId, setCollectionNamesCache, t]
  );

  const toggleExpanded = useCallback(
    (row: CollectionSelectItem) => {
      const next = !expandedRootIds[row.id];
      setExpandedRootIds((prev) => ({ ...prev, [row.id]: next }));
      if (next) void loadChildren(row);
    },
    [expandedRootIds, loadChildren]
  );

  const toggleRoot = useCallback(
    (rootId: string) => {
      if (apps.includes(rootId)) {
        const childIds = new Set((childrenByRootId[rootId] ?? []).map((c) => c.id));
        onSelectionChange({
          apps: apps.filter((x) => x !== rootId),
          kb: kb.filter((id) => !childIds.has(id)),
        });
      } else {
        onSelectionChange({
          apps: [...apps, rootId],
          kb,
        });
      }
    },
    [childrenByRootId, apps, onSelectionChange, kb]
  );

  const toggleRecordGroup = useCallback(
    (id: string) => {
      const nextKb = kb.includes(id) ? kb.filter((x) => x !== id) : [...kb, id];
      onSelectionChange({ apps, kb: nextKb });
    },
    [apps, onSelectionChange, kb]
  );

  // In 'collections' mode: flat list of KB items.
  // Strategy A: children of KB container rows (the classic expand-to-show pattern).
  // Strategy B: root items with nodeType === 'kb' shown directly (if hub returns them at root).
  // Both are de-duped and search-filtered.
  const flatKbItems = useMemo(() => {
    if (filterMode !== 'collections') return [];
    const query = searchQuery.toLowerCase();
    const seen = new Set<string>();
    const all: { id: string; name: string }[] = [];

    // A: children of recognised KB container rows
    for (const row of collections) {
      if (!isKbCollectionsConnectorRow(row) && !isCollectionScopeExpandableRow(row)) continue;
      for (const kid of childrenByRootId[row.id] ?? []) {
        if (seen.has(kid.id)) continue;
        seen.add(kid.id);
        if (!query || kid.name.toLowerCase().includes(query)) {
          all.push(kid);
        }
      }
    }

    // B: root-level kb nodes (in case the hub returns them without a container wrapper)
    for (const row of collections) {
      if (row.nodeType !== 'kb') continue;
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      if (!query || row.name.toLowerCase().includes(query)) {
        all.push({ id: row.id, name: row.name });
      }
    }

    return all;
  }, [filterMode, collections, childrenByRootId, searchQuery]);

  // True while KB container children are still being fetched — prevents premature empty state.
  // Uses childrenByRootId (state) so re-renders fire when it changes.
  const isLoadingKbChildren = useMemo(() => {
    if (filterMode !== 'collections' || isLoading) return false;
    return collections.some(
      (row) =>
        (isKbCollectionsConnectorRow(row) || isCollectionScopeExpandableRow(row)) &&
        childrenByRootId[row.id] === undefined
    );
  }, [filterMode, isLoading, collections, childrenByRootId]);

  const groupedCollections = useMemo(() => {
    // In 'collections' mode we render flatKbItems instead of this grouped list
    if (filterMode === 'collections') return [];

    let filtered = searchQuery
      ? collections.filter((c) =>
          c.name.toLowerCase().includes(searchQuery.toLowerCase())
        )
      : collections;

    // 'connectors' mode: show only non-KB connector app roots
    if (filterMode === 'connectors') {
      filtered = filtered.filter(
        (c) => !isKbCollectionsConnectorRow(c) && !isCollectionScopeExpandableRow(c)
      );
    }

    const groups = groupByTime(filtered, (c) => c.updatedAt);
    return getNonEmptyGroups(groups, (c) => c.updatedAt);
  }, [collections, searchQuery, filterMode]);

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
        {(isLoading || isLoadingKbChildren) && (
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
        {filterMode === 'collections' && !isLoading && !isLoadingKbChildren && !hasError && (
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
                const expanded = Boolean(expandedRootIds[col.id]);
                const children = childrenByRootId[col.id] ?? [];
                const childErr = childrenErrorByRootId[col.id];
                const canExpand = showExpandChevron(col);

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
                            checked={apps.includes(col.id)}
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
                          {isKbCollectionsConnectorRow(col) ? (
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
                      {canExpand ? (
                        <IconButton
                          type="button"
                          size="1"
                          variant="ghost"
                          color="gray"
                          aria-expanded={expanded}
                          aria-label={expanded ? t('common.collapse') : t('common.expand')}
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleExpanded(col);
                          }}
                        >
                          <MaterialIcon
                            name="chevron_right"
                            size={18}
                            color="var(--slate-11)"
                            style={{
                              transform: expanded ? 'rotate(90deg)' : undefined,
                              transition: 'transform 0.15s ease',
                            }}
                          />
                        </IconButton>
                      ) : null}
                    </Flex>

                    {expanded && loadingChildrenRootIds.has(col.id) ? (
                      <Flex justify="center" style={{ padding: 'var(--space-2)' }}>
                        <LottieLoader variant="loader" size={32} />
                      </Flex>
                    ) : null}

                    {expanded && childErr ? (
                      <Text size="1" style={{ color: 'var(--red-9)', paddingLeft: 'var(--space-4)' }}>
                        {childErr}
                      </Text>
                    ) : null}

                    {expanded &&
                      !childErr &&
                      children.map((ch) => (
                        <Flex
                          key={ch.id}
                          style={{
                            width: '100%',
                            minWidth: 0,
                            paddingLeft: 'var(--space-4)',
                            boxSizing: 'border-box',
                          }}
                        >
                          <CollectionRow
                            id={ch.id}
                            name={ch.name}
                            isSelected={kb.includes(ch.id)}
                            onToggle={() => toggleRecordGroup(ch.id)}
                          />
                        </Flex>
                      ))}

                    {expanded && !childErr && childrenMetaByRootId[col.id]?.hasMore ? (
                      <Flex
                        align="center"
                        style={{
                          width: '100%',
                          minWidth: 0,
                          paddingLeft: 'var(--space-4)',
                          paddingTop: 'var(--space-1)',
                          boxSizing: 'border-box',
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => void loadMoreChildren(col)}
                          disabled={loadingMoreRootId === col.id}
                          style={{
                            background: 'none',
                            border: 'none',
                            cursor: loadingMoreRootId === col.id ? 'default' : 'pointer',
                            color: 'var(--olive-9)',
                            fontSize: 12,
                            padding: 0,
                            textAlign: 'left',
                          }}
                        >
                          {loadingMoreRootId === col.id
                            ? t('agentBuilder.loadingMore')
                            : t('agentBuilder.loadMore')}
                        </button>
                      </Flex>
                    ) : null}
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
