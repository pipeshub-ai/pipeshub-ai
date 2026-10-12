import { useChatStore } from '@/chat/store';
import type { AppliedFilterNode, AppliedFilters, ChatKnowledgeFilters, CollectionMeta } from '@/chat/types';
import { bucketFor, SELECTION_KEYS } from '@/chat/utils/tree-selection';

const BUCKETS = SELECTION_KEYS;

// Older chats stored one app for all collections; it no longer exists, and
// sending its id would search nothing.
const LEGACY_COLLECTIONS_APP_PREFIX = 'knowledgeBase_';

function restorable(applied: AppliedFilters): AppliedFilters {
  return {
    ...applied,
    apps: applied.apps.filter((node) => !node.id.startsWith(LEGACY_COLLECTIONS_APP_PREFIX)),
  };
}

/**
 * The selection a stored message carried, as chat filters. Collections that an
 * older message stored under `kb` come back under `apps`, where they are sent now.
 */
export function filtersFromAppliedFilters(applied: AppliedFilters): ChatKnowledgeFilters {
  const kept = restorable(applied);
  const ids = (bucket: (typeof BUCKETS)[number]) => (kept[bucket] ?? []).map((node) => node.id);
  return {
    apps: [...new Set([...ids('apps'), ...ids('kb')])],
    kb: [],
    recordGroups: ids('recordGroups'),
    records: ids('records'),
    ...(ids('recordsExact').length ? { recordsExact: ids('recordsExact') } : {}),
  };
}

/**
 * Put a stored message's selection back in the composer, with the names its
 * pills need. A saved-agent chat narrows the agent's own apps and collections,
 * which it keeps apart.
 */
export function restoreAppliedFilters(applied: AppliedFilters, forAgentChat: boolean): void {
  const store = useChatStore.getState();
  const kept = restorable(applied);
  if (forAgentChat) {
    const ids = (bucket: (typeof BUCKETS)[number]) => (kept[bucket] ?? []).map((node) => node.id);
    store.setAgentKnowledgeScope({
      apps: ids('apps'),
      kb: ids('kb'),
      ...(ids('recordGroups').length ? { recordGroups: ids('recordGroups') } : {}),
      ...(ids('records').length ? { records: ids('records') } : {}),
      ...(ids('recordsExact').length ? { recordsExact: ids('recordsExact') } : {}),
    });
  } else {
    store.setFilters(filtersFromAppliedFilters(kept));
  }
  const names: Record<string, string> = {};
  const meta: Record<string, CollectionMeta> = {};
  for (const bucket of BUCKETS) {
    for (const node of kept[bucket] ?? []) {
      names[node.id] = node.name;
      // Where it sits in the tree is not stored with a message; keep it when this session knows it.
      const ancestorIds = store.collectionMetaCache[node.id]?.ancestorIds;
      meta[node.id] = {
        name: node.name,
        nodeType: node.nodeType,
        connector: node.connector,
        ...(ancestorIds ? { ancestorIds } : {}),
      };
    }
  }
  store.setCollectionNamesCache(names);
  store.setCollectionMetaCache(meta);
}

/** A list of nodes as stored filters, each under the key its type is sent with. */
export function appliedFiltersFromNodes(nodes: AppliedFilterNode[]): AppliedFilters {
  const applied = { apps: [], kb: [], recordGroups: [], records: [] } satisfies AppliedFilters as Required<
    Pick<AppliedFilters, 'apps' | 'kb' | 'recordGroups' | 'records'>
  >;
  for (const node of nodes) applied[bucketFor(node.nodeType)].push(node);
  return applied;
}
