'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Box, Checkbox, Flex, Spinner, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import type { KnowledgeHubNode, NodeType } from '@/app/(main)/knowledge-base/types';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';
import { useChatStore } from '@/chat/store';
import type { ChatKnowledgeFilters } from '@/chat/types';
import { BELOW_APP_KEYS, checkStateOf, selectedIds, toggleNode } from '@/chat/utils/tree-selection';
import { CollectionLeadingIcon } from './collection-row';

const CHILDREN_PAGE_SIZE = 20;
const INDENT_PER_LEVEL_PX = 20;
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
        ancestorIds: node.ancestorIds,
      },
    });
  }, [node.id, node.name, node.nodeType, node.connector, node.ancestorIds, setCollectionMetaCache, setCollectionNamesCache]);

  // A selection restored from an earlier message comes without its place in
  // the tree; its row supplies it, so the nodes above it show as partly selected.
  const placeUnknown = state === 'checked' && node.ancestorIds.length > 0 && !metaCache[node.id]?.ancestorIds;
  useEffect(() => {
    if (placeUnknown) remember();
  }, [placeUnknown, remember]);

  const locked = readOnly || state === 'inherited';
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
  selection,
}: {
  name: string;
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
        aria-label={name}
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
        alignSelf: 'stretch',
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
}

function useNodeChildren(nodeType: string, nodeId: string) {
  const [state, setState] = useState<ChildrenState>({
    items: [],
    nextCursor: null,
    status: 'loading',
    loadingMore: false,
  });

  const load = useCallback(
    async (cursor: string | null) => {
      setState((s) => (cursor ? { ...s, loadingMore: true } : { ...s, status: 'loading' }));
      try {
        const res = await KnowledgeHubApi.getNodeChildren(nodeType as NodeType, nodeId, {
          onlyContainers: false,
          limit: CHILDREN_PAGE_SIZE,
          sortBy: 'name',
          sortOrder: 'asc',
          ...(cursor ? { cursor } : {}),
        });
        setState((s) => {
          const kept = cursor ? s.items : [];
          const seen = new Set(kept.map((item) => item.id));
          return {
            items: [...kept, ...res.items.filter((item) => !seen.has(item.id))],
            nextCursor: res.pagination?.nextCursor ?? null,
            status: 'ready',
            loadingMore: false,
          };
        });
      } catch {
        setState((s) => (cursor ? { ...s, loadingMore: false } : { ...s, status: 'error' }));
      }
    },
    [nodeType, nodeId]
  );

  useEffect(() => {
    void load(null);
  }, [load]);

  return {
    ...state,
    retry: () => void load(null),
    loadMore: () => {
      if (state.nextCursor && !state.loadingMore) void load(state.nextCursor);
    },
  };
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
    return (
      <Flex align="center" style={note}>
        <Spinner size="1" />
      </Flex>
    );
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
      {items.map((item) => (
        <KnowledgeTreeRow
          key={item.id}
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
      ))}
      {nextCursor ? (
        <Flex align="center" style={note}>
          <LoadMoreLink loading={loadingMore} onClick={loadMore} />
        </Flex>
      ) : null}
    </Flex>
  );
}

function KnowledgeTreeRow({
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
          minWidth: 0,
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
        <NodeCheckbox name={node.name} selection={selection} />
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
        <Text size="2" truncate style={{ color: 'var(--slate-11)' }}>
          {node.name}
        </Text>
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
const placeAsked = new Set<string>();

/**
 * Learns where selected nodes sit in the tree when this session did not pick
 * them from it: a selection restored from a message, handed over from All
 * Records, or saved on an agent or a project. Until it is known, the nodes
 * above such a selection show unticked, and ticking one does not replace it.
 */
function useSelectionPlaces(filters: ChatKnowledgeFilters): void {
  const metaCache = useChatStore((s) => s.collectionMetaCache);
  const setCollectionMetaCache = useChatStore((s) => s.setCollectionMetaCache);
  const unplaced = BELOW_APP_KEYS.flatMap((key) => filters[key] ?? [])
    .filter((id) => !metaCache[id]?.ancestorIds && !placeAsked.has(id))
    .slice(0, MAX_PLACE_LOOKUPS)
    .join(',');

  useEffect(() => {
    for (const id of unplaced ? unplaced.split(',') : []) {
      if (placeAsked.has(id)) continue;
      placeAsked.add(id);
      const known = useChatStore.getState().collectionMetaCache[id];
      void KnowledgeHubApi.getNodeChildren((known?.nodeType || 'record') as NodeType, id, {
        onlyContainers: false,
        limit: 1,
        include: 'breadcrumbs',
      })
        .then((res) => {
          const path = res.breadcrumbs ?? [];
          const self = path[path.length - 1];
          if (self?.id !== id) return;
          setCollectionMetaCache({
            [id]: {
              name: known?.name || self.name,
              nodeType: known?.nodeType || self.nodeType,
              connector: known?.connector ?? '',
              ancestorIds: path.slice(0, -1).map((crumb) => crumb.id),
            },
          });
        })
        // Not found or not readable any more: it stays where it is, a pill without a place.
        .catch(() => undefined);
    }
  }, [unplaced, setCollectionMetaCache]);
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
