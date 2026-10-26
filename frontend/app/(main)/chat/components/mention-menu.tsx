'use client';

import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatApi, type KnowledgeBaseForChat } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { useAuthStore } from '@/lib/store/auth-store';
import type { ChatKnowledgeFilters } from '@/chat/types';
import type { MentionNode } from '@/chat/utils/node-mentions';
import {
  MENTION_SEARCH_DEBOUNCE_MS,
  MENTION_SEARCH_GROUP_ROWS,
  MENTION_SEARCH_MIN_CHARS,
  MENTION_SOURCE_ROWS,
  MENTION_SOURCE_ROWS_PER_KIND,
  filterMentionItems,
  layoutMentionGroups,
  rankMentionItems,
  type MentionGroupStatus,
  type MentionGroupView,
  type MentionMenuItem,
  type MentionMenuRow,
  type MentionMenuSection,
} from '@/chat/utils/mention-groups';
import { readRecentSources, recentSourceTimes, rememberRecentSources } from '@/chat/utils/recent-sources';
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
import { KbNodeNameIcon } from '@/knowledge-base/utils/kb-node-name-icon';
import type { KnowledgeHubNode, NodeType } from '@/knowledge-base/types';
import type { MentionParent } from '@/chat/components/mention-composer-field';

const ROOTS_PAGE_SIZE = 50;
const SEARCH_LIMIT = 20;
const ROW_HEIGHT = 40;
const ROW_HEIGHT_PHONE = 44;

/** The drill-in arrow's square: a finger needs most of a phone row's height, a pointer only the icon. */
export const openButtonSize = (rowHeight: number): number => (rowHeight >= ROW_HEIGHT_PHONE ? 40 : 24);
const LOAD_MORE_EDGE_PX = 48;
/** The box holds this height while it shows one list (a source, a group, every source), so Back does not move. */
const DRILLED_HEIGHT = 300;
const KEYS_HINT_KEY = 'chat.mentionKeysHintSeen';

const PHONE_QUERY = '(max-width: 768px)';

/** A phone-sized screen: the menu spans it and its rows are 44 px. False where the browser can't say. */
export function useMentionPhoneLayout(): boolean {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia(PHONE_QUERY);
    setPhone(query.matches);
    const onChange = (event: MediaQueryListEvent) => setPhone(event.matches);
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, []);
  return phone;
}

/** A key the composer received; a React or a DOM keyboard event. */
export interface MentionKeyEvent {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean };
  preventDefault(): void;
}

export interface MentionMenuHandle {
  /**
   * A key the composer received while the menu is open; true when the menu used it.
   * `caretAtEnd`: the caret is at the end of the `@query`, so ArrowRight has no text to move over.
   */
  keyDown: (event: MentionKeyEvent, caretAtEnd: boolean) => boolean;
}

/** What was picked: a source, or a row of a group the host added. */
export type MentionPick = { kind: 'source'; node: MentionNode } | { kind: 'item'; item: MentionMenuItem };

/** Where the Sources group starts and where it sits among the host's groups. */
export interface MentionSourcesOptions {
  /** These first (an agent's or a project's own sources); every source stays a click away. */
  preferred?: readonly MentionNode[] | null;
  /** The Sources group comes before the host's groups. */
  first?: boolean;
  /** No Sources group. */
  hidden?: boolean;
}

interface MentionMenuProps {
  query: string;
  /** Lists and searches under this node first, instead of the hub roots. */
  parent?: MentionParent | null;
  /** With these, each source row gets the picker's checkbox. */
  selection?: ChatKnowledgeFilters;
  onSelectionChange?: (next: ChatKnowledgeFilters) => void;
  onPick: (pick: MentionPick) => void;
  /** A search finished with nothing found. */
  onNoMatches?: () => void;
  /** Drops the typed filter, so a level opened from a filtered list shows all it holds. */
  onClearQuery: () => void;
  maxHeight: number;
  /** Id of the listbox; each row's id is derived from it. */
  listId: string;
  /** The active row's id, for the composer's aria-activedescendant. */
  onActiveChange: (id: string | undefined) => void;
  /** The host's own groups (already matched to `query`), besides Sources. */
  groups?: readonly MentionGroupView[];
  sources?: MentionSourcesOptions;
  /** One line under the list. `undefined` shows the keys the first time the menu ever opens; `null` shows nothing. */
  footer?: React.ReactNode | null;
}

interface Row {
  node: KnowledgeTreeNode;
  subType?: string | null;
  isPlaceholder?: boolean;
}

/** A level opened with its chevron. `ancestorIds` is unset while its place is unknown. */
interface Level {
  id: string;
  name: string;
  nodeType: string;
  connector?: string | null;
  ancestorIds?: string[];
}

/** What the box shows besides the groups: every source, one group whole, or what a source holds. */
type View = { kind: 'browse' } | { kind: 'group'; key: string } | { kind: 'level'; level: Level };

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
  const [attempt, setAttempt] = useState(0);
  const levelId = level?.id;
  const levelType = level?.nodeType;
  // A page still in flight for an earlier query or level must not land in this one.
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    if (query.length < MENTION_SEARCH_MIN_CHARS) {
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
    }, MENTION_SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, levelId, levelType, attempt]);

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

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, loadMore, retry };
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

const iconBox: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 20,
  height: 20,
  flexShrink: 0,
  lineHeight: 0,
};

function ItemRow({
  id,
  item,
  active,
  height,
  onHover,
  onPick,
  onOpen,
  checkbox,
}: {
  id: string;
  item: MentionMenuItem;
  active: boolean;
  height: number;
  onHover: () => void;
  onPick: () => void;
  onOpen: () => void;
  checkbox?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const wraps = Boolean(item.wrapDescription && item.description);
  return (
    <Flex
      align="center"
      gap="2"
      id={id}
      role="option"
      aria-selected={active}
      aria-disabled={item.disabled || undefined}
      aria-label={item.ariaLabel}
      data-mention-active={active ? '' : undefined}
      data-mention-option={item.key}
      onMouseMove={onHover}
      onMouseDown={(event) => event.preventDefault()}
      onClick={item.disabled ? undefined : onPick}
      style={{
        minHeight: height,
        height: wraps ? undefined : height,
        flexShrink: 0,
        minWidth: 0,
        padding: wraps ? '6px 6px 6px 10px' : '0 6px 0 10px',
        borderRadius: 'var(--radius-2)',
        cursor: item.disabled ? 'not-allowed' : 'pointer',
        background: active ? 'var(--olive-3)' : 'transparent',
        transition: 'background-color 0.12s',
      }}
    >
      {checkbox}
      {item.icon ? (
        <span style={{ ...iconBox, opacity: item.disabled ? 0.5 : 1 }} aria-hidden>
          {item.icon}
        </span>
      ) : null}
      <Flex
        direction={wraps ? 'column' : 'row'}
        align={wraps ? 'start' : 'center'}
        gap={wraps ? '0' : '2'}
        style={{ flex: 1, minWidth: 0 }}
      >
        <Text
          size="2"
          weight="medium"
          truncate
          title={item.label}
          data-testid="mention-label"
          style={{
            color: item.disabled ? 'var(--slate-10)' : active ? 'var(--slate-12)' : 'var(--slate-11)',
            flexShrink: item.description && !wraps ? 0 : 1,
            maxWidth: item.description && !wraps ? '65%' : '100%',
            minWidth: 0,
          }}
        >
          {item.label}
        </Text>
        {item.description ? (
          wraps ? (
            <Text size="1" data-testid="mention-description" style={{ color: 'var(--slate-11)', minWidth: 0, overflowWrap: 'anywhere', lineHeight: '16px' }}>
              {item.description}
            </Text>
          ) : (
            <>
              <Box aria-hidden style={{ width: 3, height: 3, borderRadius: '50%', background: 'var(--slate-8)', flexShrink: 0 }} />
              <Text size="2" truncate title={item.description} data-testid="mention-description" style={{ color: 'var(--slate-11)', minWidth: 0 }}>
                {item.description}
              </Text>
            </>
          )
        ) : null}
      </Flex>
      {item.trailing}
      {item.canOpen ? (
        <button
          type="button"
          tabIndex={-1}
          aria-label={t('chat.treeExpand', { name: item.label })}
          onMouseDown={(event) => event.preventDefault()}
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
          style={{
            width: openButtonSize(height),
            height: openButtonSize(height),
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

function MoreRow({
  id,
  label,
  active,
  height,
  onHover,
  onPick,
}: {
  id: string;
  label: string;
  active: boolean;
  height: number;
  onHover: () => void;
  onPick: () => void;
}) {
  return (
    <Flex
      align="center"
      gap="2"
      id={id}
      role="option"
      aria-selected={active}
      data-mention-active={active ? '' : undefined}
      data-mention-more=""
      onMouseMove={onHover}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onPick}
      style={{
        height,
        flexShrink: 0,
        padding: '0 6px 0 10px',
        borderRadius: 'var(--radius-2)',
        cursor: 'pointer',
        background: active ? 'var(--olive-3)' : 'transparent',
        transition: 'background-color 0.12s',
      }}
    >
      <Text size="2" weight="medium" truncate data-testid="mention-label" style={{ flex: 1, minWidth: 0, color: 'var(--accent-11)' }}>
        {label}
      </Text>
      <MaterialIcon name="chevron_right" size={18} color="var(--slate-10)" />
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

function RetryButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--accent-11)', fontSize: 13 }}
    >
      {t('chat.treeRetry')}
    </button>
  );
}

function SectionLabel({ id, children, hint }: { id: string; children: React.ReactNode; hint?: string }) {
  return (
    <Text
      as="div"
      id={id}
      size="1"
      weight="medium"
      style={{ color: 'var(--slate-11)', letterSpacing: '0.04px', padding: '10px 10px 6px', flexShrink: 0 }}
    >
      {children}
      {hint ? <span style={{ color: 'var(--slate-10)', fontWeight: 400 }}>{` · ${hint}`}</span> : null}
    </Text>
  );
}

function readKeysHintSeen(): boolean {
  try {
    return window.localStorage.getItem(KEYS_HINT_KEY) === '1';
  } catch {
    return true;
  }
}

function markKeysHintSeen(): void {
  try {
    window.localStorage.setItem(KEYS_HINT_KEY, '1');
  } catch {
    // Shown again next time: harmless.
  }
}

function searchRow(item: KnowledgeHubNode, metaCache: Record<string, { name: string; ancestorIds?: string[] }>): Row {
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
}

const rowOfItem = (item: MentionMenuItem): Row | null =>
  item.key.startsWith('source:') ? (item.value as Row) : null;

// ── The menu ──

/**
 * What `@` opens: the host's groups (people, agents) and the Sources group, each a few rows with "N more", or what
 * the typed name matches in each. A source opens in the same box (Back, or ← and Backspace in an empty query), and
 * "Browse all sources" lists them all. A source row's checkbox, when the composer passes a selection, scopes the chat
 * as the picker does; picking the row names it in the message.
 */
export const MentionMenu = forwardRef<MentionMenuHandle, MentionMenuProps>(function MentionMenu(
  {
    query,
    parent,
    selection,
    onSelectionChange,
    onPick,
    onNoMatches,
    onClearQuery,
    maxHeight,
    listId,
    onActiveChange,
    groups: hostGroups,
    sources = {},
    footer,
  },
  ref
) {
  const { t } = useTranslation();
  const isMobile = useMentionPhoneLayout();
  const rowHeight = isMobile ? ROW_HEIGHT_PHONE : ROW_HEIGHT;
  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const userId = useAuthStore((s) => s.user?.id ?? null);
  const [stack, setStack] = useState<View[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [atEnd, setAtEnd] = useState(true);
  const [recentTick, setRecentTick] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [showKeysHint] = useState(() => footer === undefined && !readKeysHintSeen());
  useEffect(() => {
    if (showKeysHint) markKeysHintSeen();
  }, [showKeysHint]);

  const top = stack[stack.length - 1] ?? null;
  const levels = stack.flatMap((view) => (view.kind === 'level' ? [view.level] : []));
  const opened = top?.kind === 'level' ? top.level : null;
  const parentLevel: Level | null = parent ? { id: parent.id, name: '', nodeType: parent.nodeType } : null;
  // Inside a source, or under the composer's own node, unless every source was asked for.
  const level: Level | null = opened ?? (top?.kind === 'browse' ? null : parentLevel);
  const levelPlace =
    level === null
      ? undefined
      : level.nodeType === 'app'
        ? []
        : (level.ancestorIds ?? metaCache[level.id]?.ancestorIds);

  const filter = query.trim();
  const searching = filter.length >= MENTION_SEARCH_MIN_CHARS;
  const preferred = sources.preferred && sources.preferred.length > 0 && top === null ? sources.preferred : null;
  const sourcesShown = !sources.hidden || top !== null;
  const roots = useHubRoots(sourcesShown && level === null && (preferred === null || top?.kind === 'browse'));
  const children = useNodeChildren(sourcesShown ? (level?.nodeType ?? '') : '', sourcesShown ? (level?.id ?? '') : '');
  const search = useMentionSearch(sourcesShown && searching ? filter : '', level);

  const checkable = Boolean(selection && onSelectionChange);
  useSelectionPlaces(selection ?? { apps: [], kb: [] });
  useNodePlaces(checkable && level && levelPlace === undefined ? [{ id: level.id, nodeType: level.nodeType }] : []);
  useNodePlaces(
    checkable && searching
      ? search.items.filter((item) => item.nodeType !== 'app').map((item) => ({ id: item.id, nodeType: item.nodeType }))
      : []
  );

  // eslint-disable-next-line react-hooks/exhaustive-deps -- `recentTick` re-reads what a pick stored
  const recent = useMemo(() => recentSourceTimes(readRecentSources(userId)), [userId, recentTick]);
  const sourceItem = useCallback(
    (row: Row): MentionMenuItem => ({
      key: `source:${row.node.id}`,
      label: row.node.name,
      description: row.node.subtitle,
      icon: <NodeIcon row={row} size={18} />,
      canOpen: Boolean(row.node.hasChildren),
      recentAt: recent.get(row.node.id),
      value: row,
    }),
    [recent]
  );

  const searchStatus: MentionGroupStatus = search.status === 'error' ? 'error' : search.status === 'ready' ? 'ready' : 'loading';
  const childrenReady = level !== null && children.status === 'ready' && children.loadedFor === level.id;
  const childrenStatus: MentionGroupStatus =
    level !== null && (children.loadedFor === level.id || children.status === 'error') ? children.status : 'loading';
  const ancestorIds = level && levelPlace ? [...levelPlace, level.id] : [];
  const childRows: Row[] = (childrenReady ? children.items : [])
    .filter((item) => item.name)
    .map((item) => ({
      node: {
        id: item.id,
        name: item.name,
        nodeType: item.nodeType,
        connector: item.connector ?? level?.connector,
        hasChildren: item.hasChildren,
        ancestorIds,
        placeUnknown: !levelPlace,
        mimeType: item.mimeType,
        extension: item.extension,
      },
      subType: item.subType,
      isPlaceholder: item.isPlaceholder,
    }));
  const searchItems = search.items.map((item) => sourceItem(searchRow(item, metaCache)));
  const rootItems = roots.items.filter((root) => root.name);
  const appItems = rootItems.filter((root) => !isCollectionRoot(root)).map((root) => sourceItem(rootRow(root)));
  const collectionItems = rootItems.filter(isCollectionRoot).map((root) => sourceItem(rootRow(root)));
  const browse = { label: t('chat.atMenu.browseSources', { defaultValue: 'Browse all sources' }), open: () => push({ kind: 'browse' }) };
  const sourcesLabel = t('chat.atMenu.sources', { defaultValue: 'Sources' });
  const searchGroup = (limit: number): MentionGroupView => ({
    key: 'sources',
    label: sourcesLabel,
    items: rankMentionItems(searchItems, filter),
    limit,
    status: searchStatus,
    onRetry: search.retry,
    hasMore: Boolean(search.nextCursor),
    loadMore: search.loadMore,
  });

  // The Sources group as the groups show it: a few rows, and the way to every source.
  let sourceGroups: MentionGroupView[] = [];
  if (sources.hidden) {
    sourceGroups = [];
  } else if (searching) {
    sourceGroups = [searchGroup(MENTION_SEARCH_GROUP_ROWS)];
  } else if (parentLevel) {
    sourceGroups = [
      {
        key: 'sources',
        label: sourcesLabel,
        items: filterMentionItems(childRows.map(sourceItem), filter),
        limit: MENTION_SOURCE_ROWS,
        status: childrenStatus,
        onRetry: children.retry,
        noMore: true,
      },
      { key: 'browse', items: [], limit: 0, status: 'ready', browse },
    ];
  } else if (preferred) {
    const rows = preferred.map((node) => sourceItem({ node: { ...node, ancestorIds: [], hasChildren: false } }));
    sourceGroups = [
      { key: 'sources', label: sourcesLabel, items: filterMentionItems(rows, filter), limit: MENTION_SOURCE_ROWS, status: 'ready', noMore: true },
      { key: 'browse', items: [], limit: 0, status: 'ready', browse },
    ];
  } else {
    sourceGroups = [
      {
        key: 'apps',
        label: t('chat.mentionYourApps', { defaultValue: 'Your apps' }),
        items: filterMentionItems(appItems, filter),
        limit: MENTION_SOURCE_ROWS_PER_KIND,
        status: roots.status,
        onRetry: roots.retry,
        noMore: true,
      },
      {
        key: 'collections',
        label: t('chat.mentionCollections', { defaultValue: 'Collections' }),
        items: filterMentionItems(collectionItems, filter),
        limit: MENTION_SOURCE_ROWS_PER_KIND,
        // The apps group says the roots are loading or failed; once is enough.
        status: 'ready',
        noMore: true,
      },
      ...(roots.status === 'ready' && rootItems.length > 0 ? [{ key: 'browse', items: [], limit: 0, status: 'ready' as const, browse }] : []),
    ];
  }
  const extra = hostGroups ?? [];
  const allGroups = sources.first ? [...sourceGroups, ...extra] : [...extra, ...sourceGroups];

  let sections: MentionMenuSection[] = [];
  let hasMore = false;
  let loadMore: () => void = () => {};
  let heading: string | null = null;
  const unlimited = Number.POSITIVE_INFINITY;
  if (top === null) {
    sections = layoutMentionGroups(allGroups);
  } else if (top.kind === 'group') {
    const group = top.key === 'sources' && searching ? searchGroup(unlimited) : allGroups.find((g) => g.key === top.key);
    heading = group?.label ?? null;
    sections = group ? layoutMentionGroups([group], { expanded: group.key }) : [];
    hasMore = Boolean(group?.hasMore);
    loadMore = group?.loadMore ?? (() => {});
  } else if (top.kind === 'browse') {
    heading = t('chat.atMenu.allSources', { defaultValue: 'All sources' });
    if (searching) {
      sections = layoutMentionGroups([searchGroup(unlimited)], { expanded: 'sources' });
      hasMore = Boolean(search.nextCursor);
      loadMore = search.loadMore;
    } else {
      sections = layoutMentionGroups(
        [
          { key: 'apps', label: t('chat.mentionYourApps', { defaultValue: 'Your apps' }), items: filterMentionItems(appItems, filter), limit: unlimited, status: roots.status, onRetry: roots.retry, noMore: true },
          { key: 'collections', label: t('chat.mentionCollections', { defaultValue: 'Collections' }), items: filterMentionItems(collectionItems, filter), limit: unlimited, status: 'ready', noMore: true },
        ],
        { maxRows: unlimited },
      );
      hasMore = Boolean(roots.nextCursor);
      loadMore = roots.loadMore;
    }
  } else {
    heading = levels.map((l) => l.name).join(' / ');
    if (searching) {
      sections = layoutMentionGroups([{ ...searchGroup(unlimited), label: undefined }], { expanded: 'sources' });
      hasMore = Boolean(search.nextCursor);
      loadMore = search.loadMore;
    } else {
      sections = layoutMentionGroups(
        [{ key: `level:${top.level.id}`, items: filterMentionItems(childRows.map(sourceItem), filter), limit: unlimited, status: childrenStatus, onRetry: children.retry, noMore: true }],
        { expanded: `level:${top.level.id}` },
      );
      // An empty level that loaded is still a section: it says so.
      if (sections[0]) sections[0] = { ...sections[0], group: { ...sections[0].group, status: childrenStatus } };
      hasMore = Boolean(children.nextCursor);
      loadMore = children.loadMore;
    }
  }

  const rows: MentionMenuRow[] = sections.flatMap((section) => section.rows);
  const loading = sections.some((section) => section.group.status === 'loading');
  const viewKey = `${stack.length}|${top?.kind ?? 'groups'}|${opened?.id ?? ''}|${searching ? filter : ''}`;
  const clampedIndex = Math.min(activeIndex, Math.max(rows.length - 1, 0));

  useEffect(() => {
    setActiveIndex(0);
    listRef.current?.scrollTo?.({ top: 0 });
  }, [viewKey]);

  useEffect(() => {
    listRef.current?.querySelector('[data-mention-active]')?.scrollIntoView?.({ block: 'nearest' });
  }, [clampedIndex]);

  const activeId = rows.length > 0 ? `${listId}-${clampedIndex}` : undefined;
  useEffect(() => {
    onActiveChange(activeId);
  }, [activeId, onActiveChange]);
  useEffect(() => () => onActiveChange(undefined), [onActiveChange]);

  const settled = !sections.some((section) => section.group.status === 'loading') && (!searching || search.status === 'ready' || sources.hidden);
  const noMatches = searching && settled && rows.every((row) => row.type !== 'item');
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

  useLayoutEffect(measureEnd, [rows.length, loading, measureEnd]);

  function push(view: View) {
    setStack((views) => [...views, view]);
  }

  const open = useCallback(
    (row: Row) => {
      if (!row.node.hasChildren) return;
      push({
        kind: 'level',
        level: {
          id: row.node.id,
          name: row.node.name,
          nodeType: row.node.nodeType,
          connector: row.node.connector,
          ancestorIds: row.node.placeUnknown ? undefined : row.node.ancestorIds,
        },
      });
      if (filter) onClearQuery();
    },
    [filter, onClearQuery]
  );

  const back = useCallback(() => {
    setStack((views) => views.slice(0, -1));
    if (filter) onClearQuery();
  }, [filter, onClearQuery]);

  const pickItem = useCallback(
    (item: MentionMenuItem) => {
      if (item.disabled) return;
      const row = rowOfItem(item);
      if (!row) {
        onPick({ kind: 'item', item });
        return;
      }
      const node: MentionNode = {
        id: row.node.id,
        name: row.node.name,
        nodeType: row.node.nodeType,
        connector: row.node.connector ?? '',
        extension: row.node.extension,
        mimeType: row.node.mimeType,
      };
      rememberRecentSources(userId, [node]);
      setRecentTick((n) => n + 1);
      onPick({ kind: 'source', node });
    },
    [onPick, userId]
  );

  const activate = useCallback(
    (row: MentionMenuRow) => {
      if (row.type === 'item') {
        pickItem(row.item);
        return;
      }
      if (row.browse) {
        row.browse.open();
        if (filter) onClearQuery();
        return;
      }
      push({ kind: 'group', key: row.groupKey });
      const group = allGroups.find((g) => g.key === row.groupKey);
      if (group?.hasMore) group.loadMore?.();
    },
    [pickItem, filter, onClearQuery, allGroups]
  );

  useImperativeHandle(
    ref,
    () => ({
      keyDown: (event, caretAtEnd) => {
        const row = rows[clampedIndex];
        const composing = event.isComposing ?? event.nativeEvent?.isComposing ?? false;
        // Selection, word and line moves, and IME input belong to the text.
        if (composing || event.altKey || event.ctrlKey || event.metaKey) return false;
        if (event.shiftKey && event.key !== 'Enter') return false;
        switch (event.key) {
          case 'ArrowDown':
            if (rows.length === 0) return false;
            event.preventDefault();
            setActiveIndex((clampedIndex + 1) % rows.length);
            if (clampedIndex + 1 >= rows.length - 1 && hasMore) loadMore();
            return true;
          case 'ArrowUp':
            if (rows.length === 0) return false;
            event.preventDefault();
            setActiveIndex((clampedIndex - 1 + rows.length) % rows.length);
            return true;
          case 'ArrowRight': {
            const source = row?.type === 'item' ? rowOfItem(row.item) : null;
            if (!caretAtEnd || !source?.node.hasChildren) return false;
            event.preventDefault();
            open(source);
            return true;
          }
          case 'ArrowLeft':
          case 'Backspace':
            if (query !== '' || stack.length === 0) return false;
            event.preventDefault();
            back();
            return true;
          case 'Enter':
          case 'Tab': {
            if (event.shiftKey) return false;
            const group = row ? sections.find((section) => section.group.key === row.groupKey)?.group : undefined;
            // The rows shown are the previous query's until this one's search answers.
            if (searching && group?.status === 'loading') {
              event.preventDefault();
              return true;
            }
            if (row) {
              event.preventDefault();
              activate(row);
              return true;
            }
            // Nothing to pick yet; once nothing matches, the key does what it does in the text (Enter sends).
            if (loading) {
              event.preventDefault();
              return true;
            }
            return false;
          }
          default:
            return false;
        }
      },
    }),
    [rows, clampedIndex, hasMore, loadMore, open, back, stack.length, query, sections, searching, activate, loading]
  );

  const empty =
    searching || filter
      ? t('chat.atMenu.noMatches', { defaultValue: 'No matches' })
      : opened
        ? t('chat.treeEmpty')
        : t('chat.noCollectionsAvailable');
  const nothingShown = rows.length === 0 && !loading && !sections.some((section) => section.group.status === 'error');
  const showShortHint = nothingShown && !searching && filter.length > 0;
  const drilled = top !== null;
  const boxHeight = drilled ? Math.min(DRILLED_HEIGHT, maxHeight) : undefined;
  const footerNode =
    footer === undefined
      ? showKeysHint
        ? t('chat.atMenu.keysHint', { defaultValue: '↑ ↓ to move · Enter to pick · → to open a source · Esc to close' })
        : null
      : footer;
  const backLabel = t('chat.mentionBack', { defaultValue: 'Back' });
  let rowIndex = 0;

  return (
    <Flex
      direction="column"
      style={{ maxHeight, height: boxHeight, minWidth: 0 }}
    >
      {drilled ? (
        <Flex align="center" gap="2" style={{ padding: '6px 6px 8px', flexShrink: 0, minWidth: 0 }}>
          <button
            type="button"
            aria-label={backLabel}
            onMouseDown={(event) => event.preventDefault()}
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
          {opened ? (
            <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', lineHeight: 0 }} aria-hidden>
              <NodeIcon row={{ node: { ...levels[0], ancestorIds: [] } }} size={16} />
            </span>
          ) : null}
          <Text size="2" weight="bold" truncate data-testid="mention-breadcrumb" style={{ color: 'var(--slate-12)', minWidth: 0 }}>
            {heading}
          </Text>
        </Flex>
      ) : null}
      <Box
        ref={listRef}
        id={listId}
        role="listbox"
        aria-label={t('chat.atMenu.label', { defaultValue: 'Mention suggestions' })}
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
        {showShortHint ? (
          <Note>{t('chat.nodeSearchTooShort', { defaultValue: 'Type at least 2 characters' })}</Note>
        ) : nothingShown ? (
          <Note>{empty}</Note>
        ) : (
          sections.map((section) => {
            const { group } = section;
            const labelId = `${listId}-g-${group.key}`;
            const label = drilled ? undefined : group.label;
            return (
              <div
                key={group.key}
                role="group"
                aria-labelledby={label ? labelId : undefined}
                aria-label={label ? undefined : (heading ?? group.label)}
                style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
              >
                {label ? (
                  <SectionLabel id={labelId} hint={group.hint}>
                    {label}
                  </SectionLabel>
                ) : null}
                {section.rows.length === 0 && group.status === 'loading' ? (
                  <Box style={{ padding: '4px 10px' }} data-testid={`mention-group-loading-${group.key}`}>
                    <TreeLoadShimmer />
                  </Box>
                ) : section.rows.length === 0 && group.status === 'error' ? (
                  <Note tone="error" action={group.onRetry ? <RetryButton onClick={group.onRetry} /> : null}>
                    {searching ? t('chat.treeSearchFailed') : t('chat.treeLoadFailed')}
                  </Note>
                ) : section.rows.length === 0 && drilled ? (
                  <Note>{empty}</Note>
                ) : (
                  section.rows.map((row) => {
                    const index = rowIndex++;
                    const id = `${listId}-${index}`;
                    const active = index === clampedIndex;
                    const hover = () => {
                      if (index !== clampedIndex) setActiveIndex(index);
                    };
                    if (row.type === 'more') {
                      const moreLabel = row.browse
                        ? row.browse.label
                        : row.count === null
                          ? t('chat.atMenu.showMore', { defaultValue: 'Show more' })
                          : t('chat.atMenu.nMore', { count: row.count, defaultValue: '{{count}} more' });
                      return (
                        <MoreRow
                          key={`more:${group.key}`}
                          id={id}
                          label={moreLabel}
                          active={active}
                          height={rowHeight}
                          onHover={hover}
                          onPick={() => activate(row)}
                        />
                      );
                    }
                    const source = rowOfItem(row.item);
                    return (
                      <ItemRow
                        key={row.item.key}
                        id={id}
                        item={row.item}
                        active={active}
                        height={rowHeight}
                        onHover={hover}
                        onPick={() => pickItem(row.item)}
                        onOpen={() => (source ? open(source) : undefined)}
                        checkbox={
                          source && selection && onSelectionChange ? (
                            <RowCheckbox node={source.node} filters={selection} onSelectionChange={onSelectionChange} />
                          ) : undefined
                        }
                      />
                    );
                  })
                )}
              </div>
            );
          })
        )}
        {(searching ? search.loadingMore : roots.loadingMore || children.loadingMore) ? (
          <Box style={{ padding: '0 10px' }}>
            <TreeLoadShimmer />
          </Box>
        ) : null}
      </Box>
      {footerNode ? (
        <Text
          as="div"
          size="1"
          role="note"
          data-testid="mention-footer"
          style={{ color: 'var(--slate-11)', padding: '6px 8px 2px', borderTop: '1px solid var(--olive-3)', marginTop: 4, flexShrink: 0 }}
        >
          {footerNode}
        </Text>
      ) : null}
    </Flex>
  );
});
