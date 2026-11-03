'use client';

import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatApi, type KnowledgeBaseForChat } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { useAuthStore } from '@/lib/store/auth-store';
import type { ChatKnowledgeFilters } from '@/chat/types';
import type { MentionNode } from '@/chat/utils/node-mentions';
import { CollectionLeadingIcon } from '@/chat/components/chat-panel/expansion-panels/connectors-collections/collection-row';
import {
  NodeCheckbox,
  TreeLoadShimmer,
  useNodeChildren,
  useNodePlaces,
  useNodeSelection,
  useSelectionPlaces,
  type KnowledgeTreeNode,
} from '@/chat/components/chat-panel/expansion-panels/connectors-collections/knowledge-tree';
import { KnowledgeHubApi } from '@/knowledge-base/api';
import { KB_MIN_SEARCH_QUERY_LENGTH } from '@/knowledge-base/utils';
import { KbNodeNameIcon } from '@/knowledge-base/utils/kb-node-name-icon';
import type { KnowledgeHubNode, NodeType } from '@/knowledge-base/types';
import type { MentionParent } from '@/chat/components/mention-composer-field';

const ROOTS_PAGE_SIZE = 50;
const SEARCH_LIMIT = 20;
const ROW_HEIGHT = 40;
const LOAD_MORE_EDGE_PX = 48;

export interface MentionMenuHandle {
  /**
   * A key the composer received while the menu is open; true when the menu used it.
   * `caretAtEnd`: the caret is at the end of the `@query`, so ArrowRight has no text to move over.
   */
  keyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>, caretAtEnd: boolean) => boolean;
}

interface MentionMenuProps {
  query: string;
  /** Lists and searches under this node instead of the hub roots. */
  parent?: MentionParent | null;
  /** With these, each row gets the picker's checkbox. */
  selection?: ChatKnowledgeFilters;
  onSelectionChange?: (next: ChatKnowledgeFilters) => void;
  onPick: (node: MentionNode) => void;
  /** A search finished with nothing found. */
  onNoMatches?: () => void;
  /** Drops the typed filter, so a level opened from a filtered list shows all it holds. */
  onClearQuery: () => void;
  maxHeight: number;
  /** Id of the listbox; each row's id is derived from it. */
  listId: string;
  /** The active row's id, for the composer's aria-activedescendant. */
  onActiveChange: (id: string | undefined) => void;
}

interface Row {
  node: KnowledgeTreeNode;
  subType?: string | null;
  isPlaceholder?: boolean;
}

interface Section {
  key: string;
  label?: string;
  rows: Row[];
}

/** A level opened with its chevron. `ancestorIds` is unset while its place is unknown. */
interface Level {
  id: string;
  name: string;
  nodeType: string;
  connector?: string | null;
  ancestorIds?: string[];
}

function isCollectionRoot(root: KnowledgeBaseForChat): boolean {
  return (root.origin ?? '').toString().trim().toUpperCase() === 'COLLECTION';
}

function rootRow(root: KnowledgeBaseForChat): Row {
  const connector = root.connector?.trim() || root.subType?.trim() || (isCollectionRoot(root) ? 'KB' : '');
  // A connector's own name is often just the app's; the app is worth showing only when it adds something.
  const app = isCollectionRoot(root) || connector.toUpperCase() === 'KB' ? '' : connector;
  return {
    node: {
      id: root.id,
      name: root.name,
      nodeType: 'app',
      connector,
      hasChildren: root.hasChildren,
      ancestorIds: [],
      subtitle: app && app.toLowerCase() !== root.name.trim().toLowerCase() ? app : undefined,
    },
    subType: root.subType,
  };
}

function nameMatches(name: string, filter: string): boolean {
  return !filter || name.toLowerCase().includes(filter.toLowerCase());
}

// ── Hub roots: connector apps and collections ──

interface RootsState {
  items: KnowledgeBaseForChat[];
  nextCursor: string | null;
  status: 'loading' | 'ready' | 'error';
  loadingMore: boolean;
}

/**
 * Last roots seen, so reopening the menu shows them at once while they are
 * fetched again. Kept per user: the desktop app signs out without a page load.
 */
let rootsSeen: { userId: string; items: KnowledgeBaseForChat[] } | null = null;

function seenRoots(): KnowledgeBaseForChat[] | null {
  const userId = useAuthStore.getState().user?.id;
  return userId && rootsSeen?.userId === userId ? rootsSeen.items : null;
}

function useHubRoots(enabled: boolean) {
  const [state, setState] = useState<RootsState>(() => {
    const seen = seenRoots();
    return { items: seen ?? [], nextCursor: null, status: seen ? 'ready' : 'loading', loadingMore: false };
  });
  const setCollectionNamesCache = useChatStore((s) => s.setCollectionNamesCache);
  const setCollectionMetaCache = useChatStore((s) => s.setCollectionMetaCache);

  const remember = useCallback(
    (items: KnowledgeBaseForChat[]) => {
      const names: Record<string, string> = {};
      const meta: Record<string, { name: string; nodeType: string; connector: string; ancestorIds: string[] }> = {};
      for (const item of items) {
        names[item.id] = item.name;
        meta[item.id] = { name: item.name, nodeType: item.nodeType, connector: item.connector ?? '', ancestorIds: [] };
      }
      setCollectionNamesCache(names);
      setCollectionMetaCache(meta);
    },
    [setCollectionNamesCache, setCollectionMetaCache]
  );

  const load = useCallback(
    async (cursor: string | null) => {
      setState((s) => (cursor ? { ...s, loadingMore: true } : s.items.length ? s : { ...s, status: 'loading' }));
      try {
        const res = await ChatApi.listCollectionsForChat({ cursor, limit: ROOTS_PAGE_SIZE });
        remember(res.knowledgeBases);
        setState((s) => {
          const kept = cursor ? s.items : [];
          const seen = new Set(kept.map((item) => item.id));
          const items = [...kept, ...res.knowledgeBases.filter((item) => !seen.has(item.id))];
          const userId = useAuthStore.getState().user?.id;
          if (!cursor) rootsSeen = userId ? { userId, items } : null;
          return { items, nextCursor: res.nextCursor, status: 'ready', loadingMore: false };
        });
      } catch {
        setState((s) => (cursor ? { ...s, loadingMore: false } : { ...s, status: s.items.length ? 'ready' : 'error' }));
      }
    },
    [remember]
  );

  useEffect(() => {
    if (enabled) void load(null);
  }, [enabled, load]);

  return {
    ...state,
    retry: () => void load(null),
    loadMore: () => {
      if (state.nextCursor && !state.loadingMore) void load(state.nextCursor);
    },
  };
}

// ── Search under the current level ──

interface SearchState {
  items: KnowledgeHubNode[];
  status: 'idle' | 'loading' | 'ready' | 'error';
  /** Next page of the nested search; the search of the level alone is one page. */
  nextCursor: string | null;
  loadingMore: boolean;
}

const IDLE_SEARCH: SearchState = { items: [], status: 'idle', nextCursor: null, loadingMore: false };

function appendNamed(kept: KnowledgeHubNode[], more: KnowledgeHubNode[]): KnowledgeHubNode[] {
  const seen = new Set(kept.map((item) => item.id));
  const items = [...kept];
  for (const item of more) {
    if (!item.name || seen.has(item.id)) continue;
    seen.add(item.id);
    items.push(item);
  }
  return items;
}

function useMentionSearch(query: string, level: { id: string; nodeType: string } | null) {
  const [state, setState] = useState<SearchState>(IDLE_SEARCH);
  const levelId = level?.id;
  const levelType = level?.nodeType;
  // A page still in flight for an earlier query or level must not land in this one.
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    if (query.length < KB_MIN_SEARCH_QUERY_LENGTH) {
      setState(IDLE_SEARCH);
      return;
    }
    const controller = new AbortController();
    setState((s) => ({ items: s.items, status: 'loading', nextCursor: null, loadingMore: false }));
    const parent = levelId && levelType ? { id: levelId, nodeType: levelType } : undefined;
    const timer = window.setTimeout(() => {
      // One branch failing still shows what the other found; only both failing is an error.
      const search = (flattened: boolean) =>
        KnowledgeHubApi.searchNodes({ q: query, parent, limit: SEARCH_LIMIT, signal: controller.signal, flattened })
          .catch((error: unknown) => {
            if (controller.signal.aborted) throw error;
            return null;
          });
      // This level first: a root collection is not inside a connector, so the flattened search never returns it.
      Promise.all([search(false), search(true)])
        .then(([here, nested]) => {
          if (controller.signal.aborted) return;
          if (here === null && nested === null) {
            setState({ ...IDLE_SEARCH, status: 'error' });
            return;
          }
          setState({
            items: appendNamed(appendNamed([], here?.items ?? []), nested?.items ?? []),
            status: 'ready',
            nextCursor: nested?.pagination?.nextCursor ?? null,
            loadingMore: false,
          });
        })
        .catch(() => {
          if (!controller.signal.aborted) setState({ ...IDLE_SEARCH, status: 'error' });
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, levelId, levelType]);

  const { nextCursor, loadingMore, status } = state;
  const loadMore = useCallback(() => {
    if (!nextCursor || loadingMore || status !== 'ready') return;
    const pageOf = generation.current;
    const parent = levelId && levelType ? { id: levelId, nodeType: levelType } : undefined;
    setState((s) => ({ ...s, loadingMore: true }));
    KnowledgeHubApi.searchNodes({ q: query, parent, limit: SEARCH_LIMIT, cursor: nextCursor, flattened: true })
      .then((data) => {
        if (pageOf !== generation.current) return;
        setState((s) => ({
          ...s,
          items: appendNamed(s.items, data.items ?? []),
          nextCursor: data.pagination?.nextCursor ?? null,
          loadingMore: false,
        }));
      })
      .catch(() => {
        if (pageOf === generation.current) setState((s) => ({ ...s, loadingMore: false }));
      });
  }, [nextCursor, loadingMore, status, query, levelId, levelType]);

  return { ...state, loadMore };
}

// ── Rows ──

function RowCheckbox({
  node,
  filters,
  onSelectionChange,
}: {
  node: KnowledgeTreeNode;
  filters: ChatKnowledgeFilters;
  onSelectionChange: (next: ChatKnowledgeFilters) => void;
}) {
  const selection = useNodeSelection(node, { filters, onSelectionChange });
  return <NodeCheckbox name={node.name} description={node.subtitle} selection={selection} />;
}

function NodeIcon({ row, size }: { row: Row; size: number }) {
  const { node } = row;
  if (node.nodeType === 'app' && (node.connector ?? '').trim().toUpperCase() === 'KB') {
    return <CollectionLeadingIcon sourceType="KB" size={size} />;
  }
  return (
    <KbNodeNameIcon
      isKnowledgeHub
      nodeType={node.nodeType as NodeType}
      connector={node.connector || null}
      subType={row.subType}
      mimeType={node.mimeType}
      extension={node.extension}
      name={node.name}
      size={size}
      isPlaceholder={row.isPlaceholder}
    />
  );
}

function MenuRow({
  id,
  row,
  active,
  onHover,
  onPick,
  onOpen,
  selection,
  onSelectionChange,
}: {
  id: string;
  row: Row;
  active: boolean;
  onHover: () => void;
  onPick: () => void;
  onOpen: () => void;
  selection?: ChatKnowledgeFilters;
  onSelectionChange?: (next: ChatKnowledgeFilters) => void;
}) {
  const { t } = useTranslation();
  const { node } = row;
  return (
    <Flex
      align="center"
      gap="2"
      id={id}
      role="option"
      aria-selected={active}
      data-mention-active={active ? '' : undefined}
      onMouseMove={onHover}
      onClick={onPick}
      style={{
        height: ROW_HEIGHT,
        flexShrink: 0,
        minWidth: 0,
        padding: '0 6px 0 10px',
        borderRadius: 'var(--radius-2)',
        cursor: 'pointer',
        background: active ? 'var(--olive-3)' : 'transparent',
        transition: 'background-color 0.12s',
      }}
    >
      {selection && onSelectionChange ? (
        <RowCheckbox node={node} filters={selection} onSelectionChange={onSelectionChange} />
      ) : null}
      <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', lineHeight: 0 }} aria-hidden>
        <NodeIcon row={row} size={18} />
      </span>
      <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
        <Text
          size="2"
          weight="medium"
          truncate
          title={node.name}
          style={{ color: active ? 'var(--slate-12)' : 'var(--slate-11)', flexShrink: node.subtitle ? 0 : 1, maxWidth: node.subtitle ? '65%' : undefined, minWidth: 0 }}
        >
          {node.name}
        </Text>
        {node.subtitle ? (
          <>
            <Box aria-hidden style={{ width: 3, height: 3, borderRadius: '50%', background: 'var(--slate-8)', flexShrink: 0 }} />
            <Text size="2" truncate title={node.subtitle} style={{ color: 'var(--slate-9)', minWidth: 0 }}>
              {node.subtitle}
            </Text>
          </>
        ) : null}
      </Flex>
      {node.hasChildren ? (
        <button
          type="button"
          aria-label={t('chat.treeExpand', { name: node.name })}
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
          style={{
            width: 24,
            height: 24,
            flexShrink: 0,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            border: 'none',
            padding: 0,
            borderRadius: 'var(--radius-1)',
            background: active ? 'var(--olive-5)' : 'transparent',
            color: active ? 'var(--slate-12)' : 'var(--slate-10)',
            cursor: 'pointer',
          }}
        >
          <MaterialIcon name="chevron_right" size={18} />
        </button>
      ) : null}
    </Flex>
  );
}

function Note({ children, tone = 'muted', action }: { children: React.ReactNode; tone?: 'muted' | 'error'; action?: React.ReactNode }) {
  return (
    <Flex align="center" gap="2" role={tone === 'error' ? 'alert' : 'status'} style={{ minHeight: ROW_HEIGHT, padding: '0 10px' }}>
      <Text size="2" style={{ color: tone === 'error' ? 'var(--red-11)' : 'var(--slate-9)' }}>
        {children}
      </Text>
      {action}
    </Flex>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <Text
      as="div"
      size="1"
      style={{ color: 'var(--slate-9)', letterSpacing: '0.04px', padding: '10px 10px 6px', flexShrink: 0 }}
    >
      {children}
    </Text>
  );
}

// ── The menu ──

/**
 * What `@` opens: the hub's apps and collections, then whatever a chevron
 * opens, or what the typed name matches. A row's name inserts a mention; its
 * checkbox, when the composer passes a selection, scopes the chat as the
 * picker does.
 */
export const MentionMenu = forwardRef<MentionMenuHandle, MentionMenuProps>(function MentionMenu(
  { query, parent, selection, onSelectionChange, onPick, onNoMatches, onClearQuery, maxHeight, listId, onActiveChange },
  ref
) {
  const { t } = useTranslation();
  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const [stack, setStack] = useState<Level[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [atEnd, setAtEnd] = useState(true);
  // Kept while a level is open, so the back arrow does not move as levels change size.
  const [heldHeight, setHeldHeight] = useState<number | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const opened = stack[stack.length - 1] ?? null;
  const level: Level | null = opened ?? (parent ? { id: parent.id, name: '', nodeType: parent.nodeType } : null);
  const levelPlace =
    level === null
      ? undefined
      : level.nodeType === 'app'
        ? []
        : (level.ancestorIds ?? metaCache[level.id]?.ancestorIds);

  const filter = query.trim();
  const searching = filter.length >= KB_MIN_SEARCH_QUERY_LENGTH;
  const roots = useHubRoots(level === null);
  const children = useNodeChildren(level?.nodeType ?? '', level?.id ?? '');
  const search = useMentionSearch(searching ? filter : '', level);
  const searchItems = search.items;

  const checkable = Boolean(selection && onSelectionChange);
  useSelectionPlaces(selection ?? { apps: [], kb: [] });
  useNodePlaces(checkable && level && levelPlace === undefined ? [{ id: level.id, nodeType: level.nodeType }] : []);
  useNodePlaces(
    checkable && searching
      ? searchItems.filter((item) => item.nodeType !== 'app').map((item) => ({ id: item.id, nodeType: item.nodeType }))
      : []
  );

  let sections: Section[] = [];
  let status: 'loading' | 'ready' | 'error' = 'ready';
  let retry: (() => void) | undefined;
  let hasMore = false;
  let loadMore: () => void = () => {};

  if (searching) {
    status = search.status === 'error' ? 'error' : search.status === 'ready' ? 'ready' : 'loading';
    hasMore = Boolean(search.nextCursor);
    loadMore = search.loadMore;
    sections = [
      {
        key: 'search',
        rows: searchItems.map((item) => {
          const ancestorIds = item.nodeType === 'app' ? [] : metaCache[item.id]?.ancestorIds;
          const path = (ancestorIds ?? []).map((id) => metaCache[id]?.name).filter(Boolean);
          const connector = item.connector || (item.origin === 'COLLECTION' ? 'KB' : '');
          return {
            node: {
              id: item.id,
              name: item.name,
              nodeType: item.nodeType,
              connector,
              hasChildren: item.hasChildren,
              ancestorIds: ancestorIds ?? [],
              placeUnknown: !ancestorIds,
              mimeType: item.mimeType,
              extension: item.extension,
              subtitle: path.length > 0 ? path.join(' / ') : item.parent?.name || undefined,
            },
            subType: item.subType,
            isPlaceholder: item.isPlaceholder,
          };
        }),
      },
    ];
  } else if (level) {
    status = children.loadedFor === level.id || children.status === 'error' ? children.status : 'loading';
    retry = children.retry;
    hasMore = Boolean(children.nextCursor);
    loadMore = children.loadMore;
    const ancestorIds = levelPlace ? [...levelPlace, level.id] : [];
    sections = [
      {
        key: `level:${level.id}`,
        // Until the opened level loads, the items held are the previous level's.
        rows: (children.status === 'ready' && children.loadedFor === level.id ? children.items : [])
          .filter((item) => item.name && nameMatches(item.name, filter))
          .map((item) => ({
            node: {
              id: item.id,
              name: item.name,
              nodeType: item.nodeType,
              connector: item.connector ?? level.connector,
              hasChildren: item.hasChildren,
              ancestorIds,
              placeUnknown: !levelPlace,
              mimeType: item.mimeType,
              extension: item.extension,
            },
            subType: item.subType,
            isPlaceholder: item.isPlaceholder,
          })),
      },
    ];
  } else {
    status = roots.status;
    retry = roots.retry;
    hasMore = Boolean(roots.nextCursor);
    loadMore = roots.loadMore;
    const shown = roots.items.filter(
      (root) => root.name && nameMatches(root.name, filter)
    );
    sections = [
      {
        key: 'apps',
        label: t('chat.mentionYourApps', { defaultValue: 'Your apps' }),
        rows: shown.filter((root) => !isCollectionRoot(root)).map(rootRow),
      },
      {
        key: 'collections',
        label: t('chat.mentionCollections', { defaultValue: 'Collections' }),
        rows: shown.filter(isCollectionRoot).map(rootRow),
      },
    ].filter((section) => section.rows.length > 0);
  }

  const rows = sections.flatMap((section) => section.rows);
  const viewKey = `${level?.id ?? 'root'}|${searching ? filter : ''}`;
  const clampedIndex = Math.min(activeIndex, Math.max(rows.length - 1, 0));

  useEffect(() => {
    setActiveIndex(0);
    listRef.current?.scrollTo({ top: 0 });
  }, [viewKey]);

  useEffect(() => {
    listRef.current?.querySelector('[data-mention-active]')?.scrollIntoView({ block: 'nearest' });
  }, [clampedIndex]);

  const activeId = rows.length > 0 ? `${listId}-${clampedIndex}` : undefined;
  useEffect(() => {
    onActiveChange(activeId);
  }, [activeId, onActiveChange]);
  useEffect(() => () => onActiveChange(undefined), [onActiveChange]);

  const noMatches = searching && status === 'ready' && rows.length === 0;
  const onNoMatchesRef = useRef(onNoMatches);
  useEffect(() => {
    onNoMatchesRef.current = onNoMatches;
  }, [onNoMatches]);
  useEffect(() => {
    if (noMatches) onNoMatchesRef.current?.();
  }, [noMatches, filter]);

  const measureEnd = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    const remaining = list.scrollHeight - list.scrollTop - list.clientHeight;
    setAtEnd(remaining <= 1);
    if (remaining < LOAD_MORE_EDGE_PX && hasMore) loadMore();
  }, [hasMore, loadMore]);

  useLayoutEffect(measureEnd, [rows.length, status, measureEnd]);

  const open = useCallback(
    (row: Row) => {
      if (!row.node.hasChildren) return;
      if (boxRef.current) setHeldHeight((held) => held ?? boxRef.current?.offsetHeight ?? null);
      setStack((levels) => [
        ...levels,
        {
          id: row.node.id,
          name: row.node.name,
          nodeType: row.node.nodeType,
          connector: row.node.connector,
          ancestorIds: row.node.placeUnknown ? undefined : row.node.ancestorIds,
        },
      ]);
      if (filter) onClearQuery();
    },
    [filter, onClearQuery]
  );

  const back = useCallback(() => {
    setStack((levels) => {
      const next = levels.slice(0, -1);
      if (next.length === 0) setHeldHeight(null);
      return next;
    });
    if (filter) onClearQuery();
  }, [filter, onClearQuery]);

  const pick = useCallback(
    (row: Row) =>
      onPick({
        id: row.node.id,
        name: row.node.name,
        nodeType: row.node.nodeType,
        connector: row.node.connector ?? '',
        extension: row.node.extension,
        mimeType: row.node.mimeType,
      }),
    [onPick]
  );

  useImperativeHandle(
    ref,
    () => ({
      keyDown: (event, caretAtEnd) => {
        const row = rows[clampedIndex];
        // Selection, word and line moves, and IME input belong to the text.
        if (event.nativeEvent.isComposing || event.altKey || event.ctrlKey || event.metaKey) return false;
        if (event.shiftKey && event.key !== 'Enter') return false;
        switch (event.key) {
          case 'ArrowDown':
            if (rows.length === 0) return false;
            event.preventDefault();
            setActiveIndex(Math.min(clampedIndex + 1, rows.length - 1));
            if (clampedIndex + 1 >= rows.length - 1 && hasMore) loadMore();
            return true;
          case 'ArrowUp':
            if (rows.length === 0) return false;
            event.preventDefault();
            setActiveIndex(Math.max(clampedIndex - 1, 0));
            return true;
          case 'ArrowRight':
            if (!caretAtEnd || !row?.node.hasChildren) return false;
            event.preventDefault();
            open(row);
            return true;
          case 'ArrowLeft':
            if (query !== '' || stack.length === 0) return false;
            event.preventDefault();
            back();
            return true;
          case 'Backspace':
            if (query !== '' || stack.length === 0) return false;
            event.preventDefault();
            back();
            return true;
          case 'Enter':
            if (event.shiftKey) return false;
            // The rows shown are the previous query's until this one's search answers.
            if (searching && status === 'loading') {
              event.preventDefault();
              return true;
            }
            if (row) {
              event.preventDefault();
              pick(row);
              return true;
            }
            // Nothing to pick yet; once nothing matches, Enter sends the message as typed.
            if (status === 'loading' || (!searching && filter.length > 0) || !filter) {
              event.preventDefault();
              return true;
            }
            return false;
          default:
            return false;
        }
      },
    }),
    [rows, clampedIndex, hasMore, loadMore, open, back, stack.length, query, pick, status, searching, filter]
  );

  const empty =
    searching || filter
      ? t('chat.mentionNone', { defaultValue: 'No nodes match' })
      : level
        ? t('chat.treeEmpty')
        : t('chat.noCollectionsAvailable');
  const showShortHint = !searching && filter.length > 0 && rows.length === 0 && status === 'ready';
  let rowIndex = 0;

  return (
    <Flex
      ref={boxRef}
      direction="column"
      style={{ maxHeight, minHeight: heldHeight ? Math.min(heldHeight, maxHeight) : undefined, minWidth: 0 }}
    >
      {opened ? (
        <Flex align="center" gap="2" style={{ padding: '6px 6px 8px', flexShrink: 0, minWidth: 0 }}>
          <button
            type="button"
            aria-label={t('chat.mentionBack', { defaultValue: 'Back' })}
            onClick={back}
            style={{
              width: 24,
              height: 24,
              flexShrink: 0,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: 'none',
              padding: 0,
              borderRadius: 'var(--radius-1)',
              background: 'transparent',
              color: 'var(--slate-11)',
              cursor: 'pointer',
            }}
          >
            <MaterialIcon name="arrow_back" size={18} />
          </button>
          <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', lineHeight: 0 }} aria-hidden>
            <NodeIcon row={{ node: { ...opened, ancestorIds: [] } }} size={16} />
          </span>
          <Text size="2" weight="bold" truncate style={{ color: 'var(--slate-12)', minWidth: 0 }}>
            {opened.name}
          </Text>
        </Flex>
      ) : null}
      <Box
        ref={listRef}
        id={listId}
        role="listbox"
        aria-label={t('chat.mentionNodesLabel', { defaultValue: 'Matching nodes' })}
        className="no-scrollbar"
        onScroll={measureEnd}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          maskImage: atEnd ? undefined : 'linear-gradient(to bottom, #000 calc(100% - 48px), transparent)',
          WebkitMaskImage: atEnd ? undefined : 'linear-gradient(to bottom, #000 calc(100% - 48px), transparent)',
        }}
      >
        {status === 'loading' && rows.length === 0 ? (
          <Box style={{ padding: '4px 10px' }}>
            <TreeLoadShimmer />
          </Box>
        ) : status === 'error' && rows.length === 0 ? (
          <Note
            tone="error"
            action={
              retry ? (
                <button
                  type="button"
                  onClick={retry}
                  style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--accent-11)', fontSize: 13 }}
                >
                  {t('chat.treeRetry')}
                </button>
              ) : null
            }
          >
            {searching ? t('chat.treeSearchFailed') : t('chat.treeLoadFailed')}
          </Note>
        ) : showShortHint ? (
          <Note>{t('chat.nodeSearchTooShort', { defaultValue: 'Type at least 2 characters' })}</Note>
        ) : rows.length === 0 ? (
          <Note>{empty}</Note>
        ) : (
          sections.map((section) => (
            <React.Fragment key={section.key}>
              {section.label ? <SectionLabel>{section.label}</SectionLabel> : null}
              {section.rows.map((row) => {
                const index = rowIndex++;
                return (
                  <MenuRow
                    key={row.node.id}
                    id={`${listId}-${index}`}
                    row={row}
                    active={index === clampedIndex}
                    onHover={() => {
                      if (index !== clampedIndex) setActiveIndex(index);
                    }}
                    onPick={() => pick(row)}
                    onOpen={() => open(row)}
                    selection={selection}
                    onSelectionChange={onSelectionChange}
                  />
                );
              })}
            </React.Fragment>
          ))
        )}
        {(searching ? search.loadingMore : roots.loadingMore || children.loadingMore) ? (
          <Box style={{ padding: '0 10px' }}>
            <TreeLoadShimmer />
          </Box>
        ) : null}
      </Box>
    </Flex>
  );
});
