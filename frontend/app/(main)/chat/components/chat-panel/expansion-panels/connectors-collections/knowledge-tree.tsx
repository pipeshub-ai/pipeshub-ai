'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Checkbox, Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import { KB_MIN_SEARCH_QUERY_LENGTH } from '@/app/(main)/knowledge-base/utils';
import type { KnowledgeHubNode, NodeType } from '@/app/(main)/knowledge-base/types';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters, CollectionMeta } from '@/chat/types';
import { BELOW_APP_KEYS, checkStateOf, selectedIds, toggleNode } from '@/chat/utils/tree-selection';
import { CollectionLeadingIcon } from './collection-row';

const CHILDREN_PAGE_SIZE = 20;
const INDENT_PER_LEVEL_PX = 16;
/**
 * A row never squeezes its name below this: deep rows get wider than the pane
 * instead, and the list scrolls sideways (the project settings column is 300px).
 */
const NAME_MIN_PX = 120;
const CHEVRON_BOX_PX = 24;

/** A node of the picker tree: what is listed, and where it sits. */
export interface KnowledgeTreeNode {
  id: string;
  name: string;
  nodeType: string;
  connector?: string | null;
  hasChildren?: boolean;
  /** Ids of the nodes above it, root first. */
  ancestorIds: string[];
  /** See `SelectableNode.bucket`. */
  bucket?: 'kb';
  mimeType?: string | null;
  extension?: string | null;
  /** Set on a search result whose ancestors are not known yet: `ancestorIds` is then empty. */
  placeUnknown?: boolean;
  /** Shown after the name, e.g. where a search result sits. */
  subtitle?: string;
}

interface TreeSelectionProps {
  filters: ChatKnowledgeFilters;
  onSelectionChange: (next: ChatKnowledgeFilters) => void;
  /** Shows the selection without letting it be changed. */
  readOnly?: boolean;
}

// ── Checkbox with the three states a node can be in ──

/**
 * The checkbox of a tree node. A node under a selected one is shown checked
 * and locked: its parent already includes it.
 */
export function useNodeSelection(
  node: KnowledgeTreeNode,
  { filters, onSelectionChange, readOnly = false }: TreeSelectionProps
) {
  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const setCollectionMetaCache = useChatStore((s) => s.setCollectionMetaCache);
  const setCollectionNamesCache = useChatStore((s) => s.setCollectionNamesCache);
  const ancestorsOf = useCallback((id: string) => metaCache[id]?.ancestorIds, [metaCache]);

  const state = checkStateOf(filters, node, ancestorsOf);
  const selected = new Set(selectedIds(filters));
  const includedById = state === 'inherited' ? node.ancestorIds.find((id) => selected.has(id)) : undefined;

  const remember = useCallback(() => {
    setCollectionNamesCache({ [node.id]: node.name });
    setCollectionMetaCache({
      [node.id]: {
        name: node.name,
        nodeType: node.nodeType,
        connector: node.connector ?? '',
        extension: node.extension,
        mimeType: node.mimeType,
        // An unplaced search result must not be recorded as a root: leaving the
        // place out lets the place lookup fill it in.
        ancestorIds: node.placeUnknown
          ? useChatStore.getState().collectionMetaCache[node.id]?.ancestorIds
          : node.ancestorIds,
      },
    });
  }, [node.id, node.name, node.nodeType, node.connector, node.extension, node.mimeType, node.ancestorIds, node.placeUnknown, setCollectionMetaCache, setCollectionNamesCache]);

  // A selection restored from an earlier message comes without its place in
  // the tree; its row supplies it, so the nodes above it show as partly selected.
  const rowSuppliesPlace =
    state === 'checked' && !node.placeUnknown && node.ancestorIds.length > 0 && !metaCache[node.id]?.ancestorIds;
  useEffect(() => {
    if (rowSuppliesPlace) remember();
  }, [rowSuppliesPlace, remember]);

  // Until a search result's place is known, a ticked node above it would not
  // show it as included, so ticking it could select it twice.
  const locked = readOnly || state === 'inherited' || Boolean(node.placeUnknown);
  const toggle = useCallback(() => {
    if (locked) return;
    remember();
    onSelectionChange(toggleNode(filters, node, ancestorsOf));
  }, [locked, node, filters, ancestorsOf, onSelectionChange, remember]);

  return {
    state,
    locked,
    toggle,
    /** Name of the selected ancestor that includes this node, when it is locked. */
    includedByName: includedById ? (metaCache[includedById]?.name ?? '') : '',
  };
}

export function NodeCheckbox({
  name,
  description,
  selection,
}: {
  name: string;
  /** Where the node sits, so two nodes of one name can be told apart. */
  description?: string;
  selection: ReturnType<typeof useNodeSelection>;
}) {
  const { t } = useTranslation();
  const { state, locked, toggle, includedByName } = selection;
  const checkbox = (
    <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0, lineHeight: 0 }}>
      <Checkbox
        size="1"
        variant="classic"
        checked={state === 'indeterminate' ? 'indeterminate' : state !== 'unchecked'}
        disabled={locked}
        aria-label={description ? `${name}, ${description}` : name}
        onCheckedChange={toggle}
        onClick={(e) => e.stopPropagation()}
      />
    </span>
  );
  if (state !== 'inherited' || !includedByName) return checkbox;
  return <Tooltip content={t('chat.treeIncludedWith', { name: includedByName })}>{checkbox}</Tooltip>;
}

// ── Expand arrow ──

export function ExpandArrow({
  name,
  hasChildren,
  expanded,
  onToggle,
}: {
  name: string;
  hasChildren: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  if (!hasChildren) return <span style={{ width: CHEVRON_BOX_PX, flexShrink: 0 }} aria-hidden />;
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-label={t(expanded ? 'chat.treeCollapse' : 'chat.treeExpand', { name })}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      style={{
        width: CHEVRON_BOX_PX,
        height: CHEVRON_BOX_PX,
        flexShrink: 0,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'none',
        border: 'none',
        padding: 0,
        cursor: 'pointer',
        color: 'var(--slate-10)',
      }}
    >
      <MaterialIcon name={expanded ? 'expand_more' : 'chevron_right'} size={18} />
    </button>
  );
}

export function LoadMoreLink({ loading, onClick }: { loading: boolean; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      style={{
        background: 'none',
        border: 'none',
        cursor: loading ? 'default' : 'pointer',
        color: 'var(--olive-9)',
        fontSize: 12,
        padding: 0,
        textAlign: 'left',
      }}
    >
      {loading ? t('agentBuilder.loadingMore') : t('agentBuilder.loadMore')}
    </button>
  );
}

// ── Children of one node, loaded page by page ──

interface ChildrenState {
  items: KnowledgeHubNode[];
  nextCursor: string | null;
  status: 'loading' | 'ready' | 'error';
  loadingMore: boolean;
  /** The node `items` belong to. */
  loadedFor: string;
}

const NO_CHILDREN: ChildrenState = { items: [], nextCursor: null, status: 'loading', loadingMore: false, loadedFor: '' };

/**
 * An empty `nodeId` loads nothing. When `nodeId` changes, as when the @ menu
 * opens another level, what was loaded for the old node is dropped, and a
 * response that arrives for it late is ignored: its cursor is not valid for
 * any other node.
 */
export function useNodeChildren(nodeType: string, nodeId: string) {
  const [state, setState] = useState<ChildrenState>(NO_CHILDREN);
  const currentRef = useRef(nodeId);

  const load = useCallback(
    async (cursor: string | null) => {
      setState((s) => (cursor ? { ...s, loadingMore: true } : { ...NO_CHILDREN, items: s.loadedFor === nodeId ? s.items : [] }));
      try {
        const res = await KnowledgeHubApi.getNodeChildren(nodeType as NodeType, nodeId, {
          onlyContainers: false,
          limit: CHILDREN_PAGE_SIZE,
          sortBy: 'name',
          sortOrder: 'asc',
          ...(cursor ? { cursor } : {}),
          // The row shows the failure; a node the user cannot open is not a toast.
          quiet: true,
        });
        if (currentRef.current !== nodeId) return;
        setState((s) => {
          const kept = cursor && s.loadedFor === nodeId ? s.items : [];
          const seen = new Set(kept.map((item) => item.id));
          return {
            items: [...kept, ...res.items.filter((item) => !seen.has(item.id))],
            nextCursor: res.pagination?.nextCursor ?? null,
            status: 'ready',
            loadingMore: false,
            loadedFor: nodeId,
          };
        });
      } catch {
        if (currentRef.current !== nodeId) return;
        setState((s) => (cursor ? { ...s, loadingMore: false } : { ...s, status: 'error' }));
      }
    },
    [nodeType, nodeId]
  );

  useEffect(() => {
    currentRef.current = nodeId;
    if (nodeId) void load(null);
    else setState(NO_CHILDREN);
  }, [load, nodeId]);

  const current = state.loadedFor === nodeId;
  return {
    ...state,
    // Nothing loaded for this node yet means no page after it either.
    nextCursor: current ? state.nextCursor : null,
    retry: () => void load(null),
    loadMore: () => {
      if (current && state.nextCursor && !state.loadingMore) void load(state.nextCursor);
    },
  };
}

export function TreeLoadShimmer({ indent }: { indent?: string }) {
  const widths = ['72%', '54%', '64%'];
  return (
    <Flex direction="column" role="status" aria-busy="true" style={{ width: '100%' }}>
      {widths.map((width, i) => (
        <Flex
          key={i}
          align="center"
          gap="2"
          style={{
            paddingLeft: indent,
            height: 32,
            animation: 'kb-row-in 380ms ease both',
            animationDelay: `${i * 70}ms`,
          }}
        >
          <Box
            style={{
              width: 14,
              height: 14,
              borderRadius: 3,
              flexShrink: 0,
              backgroundColor: 'var(--olive-4)',
              animation: 'shimmer-pulse 1.4s ease-in-out infinite',
              animationDelay: `${i * 70}ms`,
            }}
          />
          <Box
            style={{
              height: 10,
              width,
              maxWidth: 160,
              borderRadius: 4,
              backgroundColor: 'var(--olive-4)',
              animation: 'shimmer-pulse 1.4s ease-in-out infinite',
              animationDelay: `${i * 70}ms`,
            }}
          />
        </Flex>
      ))}
    </Flex>
  );
}

/** The rows under an expanded node. Mounted on first expand, kept while collapsed. */
export function KnowledgeTreeChildren({
  parent,
  depth,
  filters,
  onSelectionChange,
  readOnly,
}: TreeSelectionProps & { parent: KnowledgeTreeNode; depth: number }) {
  const { t } = useTranslation();
  const { items, status, nextCursor, loadingMore, retry, loadMore } = useNodeChildren(parent.nodeType, parent.id);
  const indent = `calc(var(--space-3) + ${depth * INDENT_PER_LEVEL_PX}px)`;
  const note: React.CSSProperties = { paddingLeft: indent, height: 'var(--space-6)' };

  if (status === 'loading') {
    return <TreeLoadShimmer indent={indent} />;
  }
  if (status === 'error') {
    return (
      <Flex align="center" gap="2" style={note}>
        <Text size="1" style={{ color: 'var(--red-9)' }}>
          {t('chat.treeLoadFailed')}
        </Text>
        <button
          type="button"
          onClick={retry}
          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--olive-9)', fontSize: 12 }}
        >
          {t('chat.treeRetry')}
        </button>
      </Flex>
    );
  }
  if (items.length === 0) {
    return (
      <Flex align="center" style={note}>
        <Text size="1" style={{ color: 'var(--slate-9)' }}>
          {t('chat.treeEmpty')}
        </Text>
      </Flex>
    );
  }

  const ancestorIds = [...parent.ancestorIds, parent.id];
  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      {items.map((item, index) => (
        <Box
          key={item.id}
          style={{
            animation: 'kb-row-in 320ms ease both',
            animationDelay: `${Math.min(index, 12) * 40}ms`,
          }}
        >
        <KnowledgeTreeRow
          node={{
            id: item.id,
            name: item.name,
            nodeType: item.nodeType,
            connector: item.connector ?? parent.connector,
            hasChildren: item.hasChildren,
            ancestorIds,
            mimeType: item.mimeType,
            extension: item.extension,
          }}
          depth={depth}
          filters={filters}
          onSelectionChange={onSelectionChange}
          readOnly={readOnly}
        />
        </Box>
      ))}
      {nextCursor ? (
        <Flex align="center" style={note}>
          <LoadMoreLink loading={loadingMore} onClick={loadMore} />
        </Flex>
      ) : null}
    </Flex>
  );
}

export function KnowledgeTreeRow({
  node,
  depth,
  filters,
  onSelectionChange,
  readOnly,
}: TreeSelectionProps & { node: KnowledgeTreeNode; depth: number }) {
  const [expanded, setExpanded] = useState(false);
  const [everExpanded, setEverExpanded] = useState(false);
  const [hovered, setHovered] = useState(false);
  const selection = useNodeSelection(node, { filters, onSelectionChange, readOnly });

  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      <Flex
        align="center"
        gap="2"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={selection.toggle}
        role="presentation"
        style={{
          width: '100%',
          minWidth: 'min-content',
          boxSizing: 'border-box',
          height: 'var(--space-6)',
          paddingLeft: `calc(var(--space-3) + ${depth * INDENT_PER_LEVEL_PX}px)`,
          paddingRight: 'var(--space-2)',
          borderRadius: 'var(--radius-1)',
          backgroundColor: hovered ? 'var(--olive-3)' : 'transparent',
          cursor: selection.locked ? 'default' : 'pointer',
        }}
      >
        <ExpandArrow
          name={node.name}
          hasChildren={Boolean(node.hasChildren)}
          expanded={expanded}
          onToggle={() => {
            setExpanded((open) => !open);
            setEverExpanded(true);
          }}
        />
        <NodeCheckbox name={node.name} description={node.subtitle} selection={selection} />
        <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', lineHeight: 0 }} aria-hidden>
          <KbNodeNameIcon
            isKnowledgeHub
            nodeType={node.nodeType as NodeType}
            connector={node.connector ?? null}
            mimeType={node.mimeType}
            extension={node.extension}
            name={node.name}
            size={16}
          />
        </span>
        <Text
          size="2"
          truncate
          title={node.name}
          style={{
            color: 'var(--slate-11)',
            flexShrink: node.subtitle ? 0 : 1,
            maxWidth: node.subtitle ? '60%' : undefined,
            minWidth: node.subtitle ? undefined : NAME_MIN_PX,
          }}
        >
          {node.name}
        </Text>
        {node.subtitle ? (
          <Text size="1" truncate title={node.subtitle} style={{ color: 'var(--slate-9)', minWidth: 0 }}>
            {node.subtitle}
          </Text>
        ) : null}
      </Flex>
      {everExpanded ? (
        <Box style={{ display: expanded ? 'block' : 'none', width: '100%', minWidth: 0 }}>
          <KnowledgeTreeChildren
            parent={node}
            depth={depth + 1}
            filters={filters}
            onSelectionChange={onSelectionChange}
            readOnly={readOnly}
          />
        </Box>
      ) : null}
    </Flex>
  );
}

const MAX_PLACE_LOOKUPS = 20;
const MAX_PLACE_ATTEMPTS = 3;
const PLACE_RETRY_MS = 3000;
const placeAsked = new Set<string>();
const placeAttempts = new Map<string, number>();

/**
 * Learns where nodes sit in the tree when it was not walked down to them: a
 * selection restored from a message, handed over from All Records, saved on an
 * agent or a project, or a search result. Until it is known, the nodes above
 * show unticked, and ticking one does not replace what is under it.
 *
 * `nodes` give each id the type its lookup needs; a record group looked up as a
 * record is a 400.
 */
export function useNodePlaces(nodes: Array<{ id: string; nodeType: string }>): void {
  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const setCollectionMetaCache = useChatStore((s) => s.setCollectionMetaCache);
  const [retryTick, setRetryTick] = useState(0);
  const unplaced = nodes
    .filter(({ id }) => !metaCache[id]?.ancestorIds && !placeAsked.has(id))
    .slice(0, MAX_PLACE_LOOKUPS)
    .map(({ id, nodeType }) => `${id}:${nodeType}`)
    .join(',');

  useEffect(() => {
    for (const entry of unplaced ? unplaced.split(',') : []) {
      const [id, typeHint] = entry.split(':');
      if (!id || placeAsked.has(id)) continue;
      placeAsked.add(id);
      const cachedType = useChatStore.getState().collectionMetaCache[id]?.nodeType;
      void KnowledgeHubApi.getNodeChildren((typeHint || cachedType || 'record') as NodeType, id, {
        onlyContainers: false,
        limit: 1,
        include: 'breadcrumbs',
        // A node deleted or no longer readable is not an error the user can act on here.
        quiet: true,
      })
        .then((res) => {
          const path = res.breadcrumbs ?? [];
          const self = path[path.length - 1];
          if (self?.id !== id) return;
          const cache = useChatStore.getState().collectionMetaCache;
          const update: Record<string, CollectionMeta> = {};
          // The nodes above learn their own place from the same path.
          path.slice(0, -1).forEach((crumb, i) => {
            const existing = cache[crumb.id];
            if (existing?.ancestorIds) return;
            update[crumb.id] = {
              name: existing?.name || crumb.name,
              nodeType: existing?.nodeType || crumb.nodeType,
              connector: existing?.connector ?? '',
              ancestorIds: path.slice(0, i).map((c) => c.id),
            };
          });
          // Read now, not before the request: the node may have been ticked meanwhile.
          const known = cache[id];
          update[id] = {
            name: known?.name || self.name,
            nodeType: known?.nodeType || self.nodeType,
            connector: known?.connector ?? '',
            ancestorIds: path.slice(0, -1).map((crumb) => crumb.id),
          };
          setCollectionMetaCache(update);
        })
        .catch((err: { statusCode?: number }) => {
          // Not found or not readable: a pill that says so, never its raw id.
          const status = err?.statusCode ?? 0;
          if (status >= 400 && status < 500) {
            const known = useChatStore.getState().collectionMetaCache[id];
            setCollectionMetaCache({
              [id]: {
                name: known?.name ?? '',
                nodeType: known?.nodeType || typeHint || 'record',
                connector: known?.connector ?? '',
                ancestorIds: known?.ancestorIds ?? [],
                unavailable: true,
              },
            });
            return;
          }
          const attempts = (placeAttempts.get(id) ?? 0) + 1;
          placeAttempts.set(id, attempts);
          if (attempts >= MAX_PLACE_ATTEMPTS) return;
          setTimeout(() => {
            placeAsked.delete(id);
            setRetryTick((n) => n + 1);
          }, PLACE_RETRY_MS * attempts);
        });
    }
  }, [unplaced, retryTick, setCollectionMetaCache]);
}

export function useSelectionPlaces(filters: ChatKnowledgeFilters): void {
  const groupIds = new Set(filters.recordGroups ?? []);
  useNodePlaces(
    BELOW_APP_KEYS.flatMap((key) => filters[key] ?? []).map((id) => ({
      id,
      nodeType: groupIds.has(id) ? 'recordGroup' : 'record',
    }))
  );
}

const ROOT_ROW: React.CSSProperties = {
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
  cursor: 'pointer',
};

/**
 * A top-level row of a picker (an app, a collection, or a node a scope is
 * limited to) with its checkbox, and under it, once expanded, what it holds.
 */
export function KnowledgeTreeRoot({
  node,
  filters,
  onSelectionChange,
  readOnly,
}: TreeSelectionProps & { node: KnowledgeTreeNode }) {
  const [expanded, setExpanded] = useState(false);
  const [everExpanded, setEverExpanded] = useState(false);
  useSelectionPlaces(filters);
  const selection = useNodeSelection(node, { filters, onSelectionChange, readOnly });
  const isCollection = node.nodeType === 'app' && (node.connector ?? '').trim().toUpperCase() === 'KB';

  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      <Flex
        align="center"
        gap="2"
        style={{ ...ROOT_ROW, cursor: selection.locked ? 'default' : 'pointer' }}
        onClick={selection.toggle}
        role="presentation"
      >
        <ExpandArrow
          name={node.name}
          hasChildren={Boolean(node.hasChildren)}
          expanded={expanded}
          onToggle={() => {
            setExpanded((open) => !open);
            setEverExpanded(true);
          }}
        />
        <NodeCheckbox name={node.name} selection={selection} />
        <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', lineHeight: 0 }} aria-hidden>
          {isCollection ? (
            <CollectionLeadingIcon sourceType="KB" size={20} />
          ) : (
            <KbNodeNameIcon
              isKnowledgeHub
              nodeType={node.nodeType as NodeType}
              connector={node.connector || null}
              mimeType={node.mimeType}
              extension={node.extension}
              name={node.name}
              size={20}
            />
          )}
        </span>
        <Text size="2" weight="medium" truncate style={{ color: 'var(--slate-11)' }}>
          {node.name}
        </Text>
      </Flex>
      {everExpanded ? (
        <Box style={{ display: expanded ? 'block' : 'none', width: '100%', minWidth: 0 }}>
          <KnowledgeTreeChildren
            parent={node}
            depth={1}
            filters={filters}
            onSelectionChange={onSelectionChange}
            readOnly={readOnly}
          />
        </Box>
      ) : null}
    </Flex>
  );
}

/**
 * A source that is searched only in some of its nodes: a row like a connector's,
 * and under it those nodes, as if it were opened on just them. It has no checkbox
 * of its own; ticking it would widen the search to the whole source.
 */
export function KnowledgeTreeGroup({
  title,
  icon,
  meta,
  nodes,
  filters,
  onSelectionChange,
  readOnly,
}: TreeSelectionProps & { title: string; icon: React.ReactNode; meta?: string; nodes: KnowledgeTreeNode[] }) {
  const [expanded, setExpanded] = useState(true);
  useSelectionPlaces(filters);
  const toggle = () => setExpanded((open) => !open);

  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      <Flex align="center" gap="2" style={ROOT_ROW} onClick={toggle} role="presentation">
        <ExpandArrow name={title} hasChildren expanded={expanded} onToggle={toggle} />
        <span style={{ display: 'inline-flex', flexShrink: 0, alignItems: 'center', lineHeight: 0 }} aria-hidden>
          {icon}
        </span>
        <Text size="2" weight="medium" truncate style={{ color: 'var(--slate-11)', minWidth: 0 }}>
          {title}
        </Text>
        {meta ? (
          <Text size="1" style={{ color: 'var(--slate-9)', flexShrink: 0 }}>
            {meta}
          </Text>
        ) : null}
      </Flex>
      {/* Hidden, not unmounted, so what was opened below stays open. */}
      <Flex direction="column" gap="1" style={{ display: expanded ? 'flex' : 'none', width: '100%', minWidth: 0 }}>
        {nodes.map((node) => (
          <KnowledgeTreeRow
            key={node.id}
            node={node}
            depth={1}
            filters={filters}
            onSelectionChange={onSelectionChange}
            readOnly={readOnly}
          />
        ))}
      </Flex>
    </Flex>
  );
}

// ── Search below the roots ──

const SEARCH_PAGE_SIZE = 20;

/**
 * Where a search looks: everything the user reaches (optionally only
 * connectors or only collections), or only under the given roots, as in an
 * agent's or a project's sources.
 */
export interface KnowledgeSearchScope {
  origins?: 'CONNECTOR' | 'COLLECTION';
  roots?: Array<{ id: string; nodeType: string }>;
}

type SearchParent = { id: string; nodeType: string };

/** A next page still to load, under one root, or everywhere when there is no parent. */
interface SearchPage {
  parent?: SearchParent;
  cursor: string;
}

interface SearchState {
  items: KnowledgeHubNode[];
  more: SearchPage[];
  status: 'idle' | 'loading' | 'ready' | 'error' | 'short';
  loadingMore: boolean;
}

const IDLE_SEARCH: SearchState = { items: [], more: [], status: 'idle', loadingMore: false };
const SEARCH_CONCURRENCY = 4;

/** Runs `task` over `inputs`, at most SEARCH_CONCURRENCY at a time, keeping the order. */
async function inBatches<T, R>(inputs: T[], task: (input: T) => Promise<R>): Promise<R[]> {
  const out: R[] = [];
  for (let i = 0; i < inputs.length; i += SEARCH_CONCURRENCY) {
    out.push(...(await Promise.all(inputs.slice(i, i + SEARCH_CONCURRENCY).map(task))));
  }
  return out;
}

function mergeUnique(kept: KnowledgeHubNode[], more: KnowledgeHubNode[]): KnowledgeHubNode[] {
  const seen = new Set(kept.map((item) => item.id));
  const added: KnowledgeHubNode[] = [];
  for (const item of more) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    added.push(item);
  }
  return [...kept, ...added];
}

function useKnowledgeSearch(query: string, scope: KnowledgeSearchScope) {
  const [state, setState] = useState<SearchState>(IDLE_SEARCH);
  // Roots given but none listed means nowhere to look, never everywhere.
  const rootsKey =
    scope.roots === undefined ? null : scope.roots.map((root) => `${root.nodeType}:${root.id}`).join(',');
  const origins = scope.origins;
  // One controller for the first page and every page after it: a new query or
  // scope drops whatever is still on its way.
  const controllerRef = useRef<AbortController | null>(null);

  const fetchPages = useCallback(
    async (pages: Array<{ parent?: SearchParent; cursor?: string }>, signal: AbortSignal) => {
      const results = await inBatches(pages, ({ parent, cursor }) =>
        KnowledgeHubApi.searchNodes({ q: query, parent, origins, limit: SEARCH_PAGE_SIZE, cursor, signal }).then(
          (page) => ({ parent, items: page.items, cursor: page.pagination?.nextCursor ?? null })
        )
      );
      return {
        items: results.reduce<KnowledgeHubNode[]>((all, page) => mergeUnique(all, page.items), []),
        more: results.flatMap(({ parent, cursor }) => (cursor ? [{ parent, cursor }] : [])),
      };
    },
    [query, origins]
  );

  useEffect(() => {
    controllerRef.current?.abort();
    const trimmed = query.trim();
    if (trimmed.length < KB_MIN_SEARCH_QUERY_LENGTH) {
      setState(trimmed ? { ...IDLE_SEARCH, status: 'short' } : IDLE_SEARCH);
      return;
    }
    if (rootsKey === '') {
      setState({ ...IDLE_SEARCH, status: 'ready' });
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setState({ ...IDLE_SEARCH, status: 'loading' });
    const firstPages: Array<{ parent?: SearchParent }> =
      rootsKey === null
        ? [{}]
        : rootsKey.split(',').map((entry) => {
            const at = entry.indexOf(':');
            return { parent: { nodeType: entry.slice(0, at), id: entry.slice(at + 1) } };
          });
    fetchPages(firstPages, controller.signal)
      .then(({ items, more }) => {
        if (!controller.signal.aborted) setState({ items, more, status: 'ready', loadingMore: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ ...IDLE_SEARCH, status: 'error' });
      });
    return () => controller.abort();
  }, [query, rootsKey, fetchPages]);

  const loadMore = useCallback(() => {
    const controller = controllerRef.current;
    if (!controller || state.more.length === 0 || state.loadingMore) return;
    const pages = state.more;
    setState((s) => ({ ...s, loadingMore: true }));
    fetchPages(pages, controller.signal)
      .then(({ items, more }) => {
        if (controller.signal.aborted) return;
        setState((s) => ({ ...s, items: mergeUnique(s.items, items), more, loadingMore: false }));
      })
      .catch(() => {
        if (!controller.signal.aborted) setState((s) => ({ ...s, loadingMore: false }));
      });
  }, [state.more, state.loadingMore, fetchPages]);

  return { ...state, hasMore: state.more.length > 0, loadMore };
}

/**
 * Folders, record groups and records whose name contains `query`, at any
 * depth, each with where it sits and the same checkbox as in the tree.
 * `excludeIds` are rows the picker already lists (matching roots).
 */
export function KnowledgeSearchResults({
  query,
  scope,
  excludeIds,
  emptyText,
  filters,
  onSelectionChange,
  readOnly,
}: TreeSelectionProps & {
  query: string;
  scope: KnowledgeSearchScope;
  excludeIds: Set<string>;
  /** Shown when neither the listed roots nor the search match; null when roots matched. */
  emptyText: string | null;
}) {
  const { t } = useTranslation();
  const { items, status, hasMore, loadingMore, loadMore } = useKnowledgeSearch(query, scope);
  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const shown = items.filter((item) => !excludeIds.has(item.id));
  useNodePlaces(shown.filter((item) => item.nodeType !== 'app').map((item) => ({ id: item.id, nodeType: item.nodeType })));

  const note: React.CSSProperties = { paddingLeft: 'var(--space-1)', minHeight: 'var(--space-6)' };
  if (status === 'idle') return null;
  if (status === 'short') {
    return (
      <Flex align="center" style={note} role="status">
        <Text size="1" style={{ color: 'var(--neutral-a10)' }}>
          {t('chat.nodeSearchTooShort', { defaultValue: 'Type at least 2 characters' })}
        </Text>
      </Flex>
    );
  }
  if (status === 'loading') {
    return <TreeLoadShimmer />;
  }
  if (status === 'error') {
    return (
      <Flex align="center" style={note} role="alert">
        <Text size="1" style={{ color: 'var(--red-9)' }}>
          {t('chat.treeSearchFailed')}
        </Text>
      </Flex>
    );
  }
  if (shown.length === 0) {
    return emptyText ? (
      <Flex align="center" justify="center" style={{ padding: 'var(--space-4)' }}>
        <Text size="2" style={{ color: 'var(--slate-9)' }}>
          {emptyText}
        </Text>
      </Flex>
    ) : null;
  }

  return (
    <Flex direction="column" gap="1" style={{ width: '100%', minWidth: 0 }}>
      <Flex align="center" style={{ height: 28, paddingLeft: 'var(--space-1)' }}>
        <Text size="1" style={{ color: 'var(--olive-9)' }}>
          {t('chat.treeSearchMatches')}
        </Text>
      </Flex>
      {shown.map((item, index) => {
        const ancestorIds = item.nodeType === 'app' ? [] : metaCache[item.id]?.ancestorIds;
        const path = (ancestorIds ?? []).map((id) => metaCache[id]?.name).filter(Boolean);
        return (
          <Box
            key={item.id}
            style={{
              animation: 'kb-row-in 320ms ease both',
              animationDelay: `${Math.min(index, 12) * 40}ms`,
            }}
          >
          <KnowledgeTreeRow
            node={{
              id: item.id,
              name: item.name,
              nodeType: item.nodeType,
              connector: item.connector || (item.origin === 'COLLECTION' ? 'KB' : ''),
              // Opened only once its place is known, so what is under it is placed too.
              hasChildren: Boolean(item.hasChildren && ancestorIds),
              ancestorIds: ancestorIds ?? [],
              placeUnknown: !ancestorIds,
              mimeType: item.mimeType,
              extension: item.extension,
              subtitle: path.length > 0 ? path.join(' / ') : item.parent?.name,
            }}
            depth={0}
            filters={filters}
            onSelectionChange={onSelectionChange}
            readOnly={readOnly}
          />
          </Box>
        );
      })}
      {hasMore ? (
        <Flex align="center" style={note}>
          <LoadMoreLink loading={loadingMore} onClick={loadMore} />
        </Flex>
      ) : null}
    </Flex>
  );
}
