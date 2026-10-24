'use client';

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link } from '@/lib/navigation';
import Image from 'next/image';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  Flex,
  IconButton,
  Spinner,
  Text,
  TextField,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { useChatStore, ASSISTANT_CTX, getUniversalCombinedCatalog } from '@/chat/store';
import { MAX_TOOLSETS_LIST_LIMIT, ToolsetsApi } from '@/app/(main)/toolsets/api';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectActionsEnabled,
  selectApiToolsetsEnabled,
} from '@/lib/store/feature-flags-store';
import { ApiToolsetsApi } from '@/app/(main)/workspace/api-toolsets/api';
import type { ApiToolset } from '@/app/(main)/workspace/api-toolsets/types';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { toast } from '@/lib/store/toast-store';
import { CollectionsTab } from './connectors-collections/collections-tab';
import { McpConnectRows, mcpServersToConnect } from './mcp-connect-rows';
import { ToolsetConnectRows, actionName, actionsToConnect } from './toolset-connect-rows';
import { ApiActionConnectRows, apiActionsToConnect } from './api-action-connect-rows';
import type { CollectionScopeSelection } from './connectors-collections/collections-tab';
import {
  bareToolFullName,
  buildApiActionGroups,
  buildCatalogMcpGroups,
  buildCatalogToolGroups,
  type CatalogToolGroupRow,
} from '@/chat/tool-groups';

type ExpansionViewMode = 'inline' | 'overlay';

const TAB_VALUES = ['connectors', 'collections', 'actions', 'mcp'] as const;
type TabValue = (typeof TAB_VALUES)[number];

/**
 * Width was a fixed 246px sized for 3 tabs — scaled per-tab so a 4th ("MCP") entry doesn't
 * crush the longer "Connectors"/"Collections" labels. Computed from the actually-rendered
 * tab count (`visibleTabValues`) so hiding the MCP tab shrinks the tablist back down.
 */
const FIGMA_TABLIST_WIDTH_PER_TAB = 82;
const FIGMA_TABLIST_HEIGHT = 32;
const FIGMA_TABLIST_RADIUS = 4;

function UniversalAgentFilterTablist({
  value,
  onValueChange,
  labels,
  disabledTabs = [],
  hiddenTabs = [],
}: {
  value: TabValue;
  onValueChange: (next: TabValue) => void;
  labels: Record<TabValue, string>;
  disabledTabs?: TabValue[];
  /** Tabs excluded entirely (not just disabled) — e.g. 'mcp' when ENABLE_MCP is off. */
  hiddenTabs?: TabValue[];
}) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';

  const visibleTabValues = useMemo(
    () => TAB_VALUES.filter((v) => !hiddenTabs.includes(v)),
    [hiddenTabs]
  );

  const trackStyle: React.CSSProperties = {
    width: FIGMA_TABLIST_WIDTH_PER_TAB * visibleTabValues.length,
    maxWidth: '100%',
    height: FIGMA_TABLIST_HEIGHT,
    borderRadius: FIGMA_TABLIST_RADIUS,
    display: 'flex',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
    boxSizing: 'border-box',
    flexShrink: 0,
    ...(isDark
      ? {
          background:
            'linear-gradient(90deg, rgba(221, 234, 248, 0.0784314) 0%, rgba(221, 234, 248, 0.0784314) 100%), rgba(0, 0, 0, 0.25)',
        }
      : {
          background:
            'linear-gradient(90deg, rgba(0, 0, 51, 0.059) 0%, rgba(0, 0, 51, 0.059) 100%), linear-gradient(90deg, rgba(255, 255, 255, 0.9) 0%, rgba(255, 255, 255, 0.9) 100%)',
        }),
  };

  const inactiveColor = isDark ? 'rgba(252, 253, 255, 0.937255)' : 'rgba(0, 5, 9, 0.89)';
  const selectedBorder = isDark
    ? '1px solid rgba(211, 237, 248, 0.113725)'
    : '1px solid rgba(0, 0, 45, 0.09)';
  const selectedBg = isDark ? '#111113' : 'var(--color-panel-solid, #ffffff)';

  const onKeyDown = (e: React.KeyboardEvent) => {
    const i = visibleTabValues.indexOf(value);
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      const next = visibleTabValues[(i + 1) % visibleTabValues.length]!;
      if (!disabledTabs.includes(next)) onValueChange(next);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      const next = visibleTabValues[(i - 1 + visibleTabValues.length) % visibleTabValues.length]!;
      if (!disabledTabs.includes(next)) onValueChange(next);
    }
  };

  return (
    <div
      role="tablist"
      aria-label="Universal agent filters"
      onKeyDown={onKeyDown}
      style={trackStyle}
    >
      {visibleTabValues.map((tabValue) => {
        const selected = value === tabValue;
        const isDisabled = disabledTabs.includes(tabValue);
        return (
          <button
            key={tabValue}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-disabled={isDisabled}
            disabled={isDisabled}
            onClick={() => { if (!isDisabled) onValueChange(tabValue); }}
            style={{
              boxSizing: 'border-box',
              flex: '1 1 0',
              minWidth: 0,
              height: FIGMA_TABLIST_HEIGHT,
              padding: '0 12px',
              gap: 4,
              display: 'flex',
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: FIGMA_TABLIST_RADIUS,
              border: selected ? selectedBorder : '1px solid transparent',
              background: selected ? selectedBg : 'rgba(255, 255, 255, 0.00001)',
              color: isDisabled ? 'var(--gray-7)' : inactiveColor,
              fontSize: 12,
              lineHeight: '16px',
              letterSpacing: '0.04px',
              fontWeight: selected ? 500 : 400,
              fontFamily: 'inherit',
              cursor: isDisabled ? 'not-allowed' : 'pointer',
              opacity: isDisabled ? 0.5 : 1,
              isolation: 'isolate',
            }}
          >
            {labels[tabValue]}
          </button>
        );
      })}
    </div>
  );
}

const OLIVE_ROW = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-1-max, 3px)',
} as const;

const CHECKBOX_ALIGN: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
  lineHeight: 0,
};

function humanizeUnderscores(value: string): string {
  return value.replace(/_/g, ' ');
}

function toolsetSubtitle(group: { fullNames: string[] }): string {
  const raw = group.fullNames[0];
  if (!raw) return '';
  const fn = bareToolFullName(raw);
  if (!fn.includes('.')) return '';
  return fn
    .slice(0, fn.indexOf('.'))
    .replace(/_/g, ' ')
    .trim();
}

function setsEqualAsSets(a: string[], b: string[]): boolean {
  if (a === b) return true;
  const sa = new Set(a);
  const sb = new Set(b);
  if (sa.size !== sb.size) return false;
  return Array.from(sa).every((x) => sb.has(x));
}

function UniversalToolsetRowIcon({
  toolsetSlug,
  label,
  iconPath,
}: {
  toolsetSlug: string;
  label: string;
  iconPath?: string;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const path = iconPath?.trim();
  const pathOk = Boolean(path && (path.startsWith('/') || path.startsWith('http')));
  if (pathOk && !imgFailed) {
    return (
      <Image
        src={path!}
        alt=""
        width={18}
        height={18}
        unoptimized
        onError={() => setImgFailed(true)}
        style={{ objectFit: 'contain', flexShrink: 0 }}
      />
    );
  }
  return <ConnectorIcon type={resolveConnectorType(toolsetSlug || label)} size={18} />;
}

/** Page size used for the paginated my-toolsets fetch in the Actions tab. */
const ACTIONS_PAGE_SIZE = 50;

/**
 * Module-level actions cache — survives tab switches (Actions ↔ Connectors/Collections)
 * within the same page session so we never re-fetch on every remount.
 *
 * `lastFetchedAt` is set on each successful page-1 fetch.  When the component mounts
 * and detects a stale cache (older than CACHE_TTL_MS) it re-fetches from page 1,
 * preventing leftover data from a prior user/session from being served.
 */
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
let _actionsPageCache: {
  toolsets: BuilderSidebarToolset[];
  page: number;
  hasMore: boolean;
  lastFetchedAt: number;
  /** The person's actions not signed in to yet, listed with a Connect button. */
  toConnect: BuilderSidebarToolset[];
  /** API actions, connected or not; not paginated, so reloaded with page 1. */
  apiActions: ApiToolset[];
  /** The API actions flag when `apiActions` was loaded: flags can arrive after the panel first loads. */
  apiActionsEnabled: boolean;
} = { toolsets: [], page: 0, hasMore: false, lastFetchedAt: 0, toConnect: [], apiActions: [], apiActionsEnabled: false };

/** Without this list the tab still shows the connected actions, so a failure only hides Connect. */
async function loadActionsToConnect(): Promise<BuilderSidebarToolset[]> {
  try {
    const { toolsets } = await ToolsetsApi.getAllMyToolsets({
      authStatus: 'not-authenticated',
      limitPerPage: MAX_TOOLSETS_LIST_LIMIT,
    });
    return toolsets;
  } catch (err) {
    console.error('[UniversalAgentResourcesPanel] Failed to load actions to connect', err);
    return [];
  }
}

/**
 * Module-level MCP cache, mirroring `_actionsPageCache`. `getMyMcpServers` isn't paginated —
 * a single fetch on mount is enough — but the same TTL-based staleness check applies so a
 * stale prior session's data isn't served after remount. Holds every server, connected or not:
 * the ones a sign-in keeps out are listed with a Connect button.
 */
let _mcpCache: {
  instances: McpMyServerEntry[];
  lastFetchedAt: number;
} = { instances: [], lastFetchedAt: 0 };

interface UniversalAgentResourcesPanelProps {
  onToggleView?: () => void;
  viewMode?: ExpansionViewMode;
}

/**
 * Connectors · Collections · Actions panel for universal agent mode
 * (main chat, `queryMode === 'agent'`, no `agentId` in URL).
 *
 * - Connectors / Collections: delegate to CollectionsTab (same data path as assistant;
 *   optional “all connectors by default” hint is derived there for main `/chat` without an agent).
 * - Actions: loads from GET /api/v1/toolsets/my-toolsets — connected instances' tools, then the
 *   ones still to sign in to, each with a Connect button.
 * - Tool selection is stored in `universalAgentStreamTools` (ASSISTANT_CTX, not agent-scoped).
 * - Shows a model-gate warning when no reasoning-capable model is configured.
 */
export function UniversalAgentResourcesPanel({
  onToggleView,
  viewMode = 'inline',
}: UniversalAgentResourcesPanelProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabValue>('connectors');
  const [search, setSearch] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const actionsEnabled = useFeatureFlagsStore(selectActionsEnabled);
  const apiActionsEnabled = useFeatureFlagsStore(selectApiToolsetsEnabled);
  const isAdmin = useUserStore(selectIsAdmin) ?? false;
  const [mcpInstances, setMcpInstances] = useState<McpMyServerEntry[]>(() => _mcpCache.instances);
  const [unconnectedActions, setUnconnectedActions] = useState<BuilderSidebarToolset[]>(
    () => _actionsPageCache.toConnect
  );
  const [apiActions, setApiActions] = useState<ApiToolset[]>(() => _actionsPageCache.apiActions);

  const settings = useChatStore((s) => s.settings);
  const setFilters = useChatStore((s) => s.setFilters);
  const agentCapabilities = settings.agentCapabilities;

  const toolGroups = useChatStore((s) => s.universalAgentToolGroups);
  const toolCatalog = useChatStore((s) => s.universalAgentToolCatalogFullNames);
  const selectedTools = useChatStore((s) => s.universalAgentStreamTools);
  const toolsLoading = useChatStore((s) => s.universalAgentToolsLoading);
  const toolsError = useChatStore((s) => s.universalAgentToolsError);
  const setSelectedTools = useChatStore((s) => s.setUniversalAgentStreamTools);
  const hydrateResources = useChatStore((s) => s.hydrateUniversalAgentResources);
  const setToolsLoading = useChatStore((s) => s.setUniversalAgentToolsLoading);
  const setToolsError = useChatStore((s) => s.setUniversalAgentToolsError);

  const mcpGroups = useChatStore((s) => s.universalAgentMcpGroups);
  const mcpCatalog = useChatStore((s) => s.universalAgentMcpCatalogFullNames);
  const mcpLoading = useChatStore((s) => s.universalAgentMcpLoading);
  const mcpError = useChatStore((s) => s.universalAgentMcpError);
  const hydrateMcpResources = useChatStore((s) => s.hydrateUniversalAgentMcpResources);
  const setMcpLoading = useChatStore((s) => s.setUniversalAgentMcpLoading);
  const setMcpError = useChatStore((s) => s.setUniversalAgentMcpError);

  /** Detect whether a reasoning model is available for the assistant context. */
  const hasReasoningModel = useMemo(() => {
    const ctxModels = settings.availableModels[ASSISTANT_CTX]?.models ?? [];
    return ctxModels.some((m) => m.isReasoning);
  }, [settings.availableModels]);

  /**
   * The model list may not have been fetched yet (availableModels is empty).
   * Only show the gate when we have at least one model entry AND none are reasoning.
   */
  const modelsLoaded = (settings.availableModels[ASSISTANT_CTX]?.models?.length ?? 0) > 0;
  const showModelGate = modelsLoaded && !hasReasoningModel;

  const selectedCount = useMemo(() => {
    if (selectedTools === null) return toolCatalog.length + mcpCatalog.length;
    return selectedTools.length;
  }, [selectedTools, toolCatalog.length, mcpCatalog.length]);

  // ── Pagination state ──
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasNextPage, setHasNextPage] = useState(false);
  const isFetchingPageRef = useRef(false);

  /**
   * Load a single page and append it to the module-level cache then rebuild groups. Page 1 also
   * reloads the actions still to connect. `quiet` (after a sign-in): refresh in place, reloading
   * every page already shown, and keep what is shown if the refresh fails.
   */
  const loadPage = useCallback(
    async (page: number, { quiet = false }: { quiet?: boolean } = {}) => {
      if (isFetchingPageRef.current) return undefined;
      isFetchingPageRef.current = true;
      const previousApiActions = _actionsPageCache.apiActions;

      if (page === 1) {
        if (!quiet) {
          _actionsPageCache = {
            toolsets: [],
            page: 0,
            hasMore: false,
            lastFetchedAt: 0,
            toConnect: [],
            apiActions: [],
            apiActionsEnabled,
          };
          setToolsLoading(true);
        }
      } else {
        setIsLoadingMore(true);
      }

      const lastPage = quiet ? Math.max(page, _actionsPageCache.page) : page;
      try {
        const [results, toConnect, apiActions] = await Promise.all([
          Promise.all(
            Array.from({ length: lastPage - page + 1 }, (_, i) =>
              ToolsetsApi.getMyToolsets({
                page: page + i,
                limit: ACTIONS_PAGE_SIZE,
                authStatus: 'authenticated',
              })
            )
          ),
          page === 1 ? loadActionsToConnect() : Promise.resolve(_actionsPageCache.toConnect),
          // A failed request keeps the last list rather than emptying it; the cache's expiry
          // retries it. (Retrying at once would loop: this effect re-runs after every load.)
          page === 1 && apiActionsEnabled
            ? ApiToolsetsApi.listInstances().catch(() => previousApiActions)
            : Promise.resolve(_actionsPageCache.apiActions),
        ]);
        const result = results[results.length - 1]!;

        _actionsPageCache = {
          toolsets: [...(page === 1 ? [] : _actionsPageCache.toolsets), ...results.flatMap((r) => r.toolsets)],
          page: lastPage,
          hasMore: result.hasNext,
          lastFetchedAt: page === 1 ? Date.now() : _actionsPageCache.lastFetchedAt,
          toConnect,
          apiActions,
          apiActionsEnabled: page === 1 ? apiActionsEnabled : _actionsPageCache.apiActionsEnabled,
        };
        setUnconnectedActions(toConnect);
        setApiActions(apiActions);

        // Rebuild from full accumulated list so instanceId-based discriminators are stable.
        const groups = [
          ...buildCatalogToolGroups(_actionsPageCache.toolsets),
          ...buildApiActionGroups(apiActions.filter((action) => action.isConnected)),
        ];
        const catalogFullNames = groups.flatMap((g) => g.fullNames);
        hydrateResources({ toolGroups: groups, toolCatalogFullNames: catalogFullNames });
        setHasNextPage(result.hasNext);
        return groups;
      } catch (err) {
        console.error('[UniversalAgentResourcesPanel] Failed to load my-toolsets page', page, err);
        if (quiet) {
          toast.error(t('chat.universalAgent.toolsLoadError', { defaultValue: 'Failed to load available actions.' }));
        } else if (page === 1) {
          // Mark cache so the initial-load effect does not retry in a tight loop on failure.
          _actionsPageCache = { ..._actionsPageCache, lastFetchedAt: Date.now() };
          setToolsError(
            t('chat.universalAgent.toolsLoadError', {
              defaultValue: 'Failed to load available actions.',
            })
          );
          setToolsLoading(false);
        }
        return undefined;
      } finally {
        if (page > 1) setIsLoadingMore(false);
        isFetchingPageRef.current = false;
      }
    },
    [apiActionsEnabled, hydrateResources, setToolsLoading, setToolsError, t]
  );

  // Initial load — skips re-fetch when the module cache has a fresh page-1 result
  // (including “success but zero actionable tools”, where toolGroups stay empty).
  // Refetches when the cache is older than CACHE_TTL_MS. Skipped entirely when
  // Actions is disabled — the tab is hidden, so there's nothing to render this
  // data into (mirrors the MCP load effect below).
  useEffect(() => {
    if (!actionsEnabled || toolsLoading) return;
    const lastAt = _actionsPageCache.lastFetchedAt;
    const cacheStale =
      lastAt <= 0 ||
      Date.now() - lastAt > CACHE_TTL_MS ||
      _actionsPageCache.apiActionsEnabled !== apiActionsEnabled;
    const hasFreshPage1 = lastAt > 0 && !cacheStale;
    if (hasFreshPage1) {
      setHasNextPage(_actionsPageCache.hasMore);
      return;
    }
    void loadPage(1);
  }, [actionsEnabled, apiActionsEnabled, toolsLoading, loadPage]);

  const handleLoadMore = useCallback(() => {
    if (!hasNextPage || isLoadingMore) return;
    void loadPage(_actionsPageCache.page + 1);
  }, [hasNextPage, isLoadingMore, loadPage]);

  // ── MCP servers (my-mcp-servers, authenticated instances, one-shot fetch) ──
  const isFetchingMcpRef = useRef(false);

  /** `quiet`: refresh in place (after a sign-in) instead of showing the loading state. */
  const loadMcp = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    if (isFetchingMcpRef.current) return undefined;
    isFetchingMcpRef.current = true;
    if (!quiet) setMcpLoading(true);
    try {
      const result = await McpServersApi.getMyMcpServers(true);
      const instances = result.instances || [];
      _mcpCache = { instances, lastFetchedAt: Date.now() };
      setMcpInstances(instances);

      const groups = buildCatalogMcpGroups(instances.filter((entry) => entry.isAuthenticated));
      const catalogFullNames = groups.flatMap((g) => g.fullNames);
      hydrateMcpResources({ mcpGroups: groups, mcpCatalogFullNames: catalogFullNames });
      return groups;
    } catch (err) {
      console.error('[UniversalAgentResourcesPanel] Failed to load my-mcp-servers', err);
      _mcpCache = { ..._mcpCache, lastFetchedAt: Date.now() };
      setMcpError(
        t('chat.universalAgent.mcpLoadError', { defaultValue: 'Failed to load MCP servers.' })
      );
      setMcpLoading(false);
      return undefined;
    } finally {
      isFetchingMcpRef.current = false;
    }
  }, [hydrateMcpResources, setMcpLoading, setMcpError, t]);

  // Initial load — skips re-fetch when the module cache has fresh data (including
  // "success but zero authenticated servers", where mcpGroups stay empty).
  // Skipped entirely when MCP is disabled — the tab is hidden, so there's
  // nothing to render this data into.
  useEffect(() => {
    if (!mcpEnabled || mcpLoading) return;
    const lastAt = _mcpCache.lastFetchedAt;
    const cacheStale = lastAt <= 0 || Date.now() - lastAt > CACHE_TTL_MS;
    if (!cacheStale) return;
    void loadMcp();
  }, [mcpEnabled, mcpLoading, loadMcp]);

  // ── Tool selection helpers (same semantics as AgentScopedResourcesPanel) ──

  const isToolOn = useCallback(
    (fullName: string) => {
      if (selectedTools === null) return true;
      return selectedTools.includes(fullName);
    },
    [selectedTools]
  );

  const toggleTool = useCallback(
    (fullName: string) => {
      const cat = getUniversalCombinedCatalog();
      const cur = useChatStore.getState().universalAgentStreamTools;

      if (cur === null) {
        const next = cat.filter((x) => x !== fullName);
        if (next.length === 0) {
          setSelectedTools([]);
        } else if (cat.length > 0 && setsEqualAsSets(next, cat)) {
          setSelectedTools(null);
        } else {
          setSelectedTools(next);
        }
        return;
      }

      let next = [...cur];
      if (next.includes(fullName)) {
        next = next.filter((x) => x !== fullName);
      } else {
        next.push(fullName);
      }
      if (next.length === 0) {
        setSelectedTools([]);
        return;
      }
      if (cat.length > 0 && setsEqualAsSets(next, cat)) {
        setSelectedTools(null);
      } else {
        setSelectedTools(next);
      }
    },
    [setSelectedTools]
  );

  const groupCheckState = useCallback(
    (fullNames: string[]): boolean | 'indeterminate' => {
      const on = fullNames.filter((fn) => isToolOn(fn)).length;
      if (on === 0) return false;
      if (on === fullNames.length) return true;
      return 'indeterminate';
    },
    [isToolOn]
  );

  const setGroupToolsEnabled = useCallback(
    (fullNames: string[], enabled: boolean) => {
      const cat = getUniversalCombinedCatalog();
      const explicit = useChatStore.getState().universalAgentStreamTools;

      if (explicit === null) {
        if (!enabled) {
          const next = cat.filter((fn) => !fullNames.includes(fn));
          if (next.length === 0) {
            setSelectedTools([]);
          } else if (cat.length > 0 && setsEqualAsSets(next, cat)) {
            setSelectedTools(null);
          } else {
            setSelectedTools(next);
          }
        }
        return;
      }

      const nextSet = new Set(explicit);
      if (enabled) {
        for (const fn of fullNames) {
          nextSet.add(fn);
        }
      } else {
        for (const fn of fullNames) nextSet.delete(fn);
      }
      const arr = Array.from(nextSet);
      if (arr.length === 0) {
        setSelectedTools([]);
        return;
      }
      if (cat.length > 0 && setsEqualAsSets(arr, cat)) {
        setSelectedTools(null);
      } else {
        setSelectedTools(arr);
      }
    },
    [setSelectedTools]
  );

  const resetToDefaults = useCallback(() => {
    setSelectedTools(null);
    setFilters({ apps: [], kb: [] });
  }, [setSelectedTools, setFilters]);

  // When internalSearch is turned off, snap tab to 'actions' if currently on a disabled tab.
  const internalSearchEnabled = agentCapabilities.internalSearch;
  const disabledTabs = useMemo<TabValue[]>(
    () => (!internalSearchEnabled ? ['connectors', 'collections'] : []),
    [internalSearchEnabled]
  );
  const hiddenTabs = useMemo<TabValue[]>(
    () => [...(mcpEnabled ? [] : (['mcp'] as TabValue[])), ...(actionsEnabled ? [] : (['actions'] as TabValue[]))],
    [mcpEnabled, actionsEnabled]
  );

  // If the active tab becomes disabled or hidden, switch to the first tab that's neither.
  React.useEffect(() => {
    if (disabledTabs.includes(tab) || hiddenTabs.includes(tab)) {
      const fallback = TAB_VALUES.find((v) => !disabledTabs.includes(v) && !hiddenTabs.includes(v));
      if (fallback) setTab(fallback);
    }
  }, [disabledTabs, hiddenTabs, tab]);

  // ── Filtered views ──

  const filterGroupsBySearch = useCallback(
    (groups: CatalogToolGroupRow[]): CatalogToolGroupRow[] =>
      groups
        .map((g) => ({
          ...g,
          fullNames: g.fullNames.filter((key) => {
            if (!search.trim()) return true;
            const q = search.toLowerCase();
            // The row shows `toolLabels` (an API action's operation summary), so search it too.
            const desc = `${g.toolDescriptions?.[key] ?? ''} ${g.toolLabels?.[key] ?? ''}`.toLowerCase();
            // Strip the instanceId prefix when comparing against the search term
            const bare = bareToolFullName(key);
            return bare.toLowerCase().includes(q) || g.label.toLowerCase().includes(q) || desc.includes(q);
          }),
        }))
        .filter((g) => g.fullNames.length > 0),
    [search]
  );

  const filteredToolGroups = useMemo(
    () => filterGroupsBySearch(toolGroups),
    [filterGroupsBySearch, toolGroups]
  );

  const filteredMcpGroups = useMemo(
    () => filterGroupsBySearch(mcpGroups),
    [filterGroupsBySearch, mcpGroups]
  );

  const mcpToConnect = useMemo(
    () => mcpServersToConnect(mcpInstances, { isAdmin, listed: new Set(mcpGroups.map((g) => g.instanceId)) }),
    [mcpInstances, isAdmin, mcpGroups]
  );
  const filteredMcpToConnect = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? mcpToConnect.filter(({ entry }) => entry.name.toLowerCase().includes(q)) : mcpToConnect;
  }, [mcpToConnect, search]);

  const toolsetsToConnect = useMemo(
    () => actionsToConnect(unconnectedActions, new Set(toolGroups.map((g) => g.instanceId))),
    [unconnectedActions, toolGroups]
  );
  const filteredToolsetsToConnect = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? toolsetsToConnect.filter((ts) => actionName(ts).toLowerCase().includes(q)) : toolsetsToConnect;
  }, [toolsetsToConnect, search]);

  const apiActionsNotConnected = useMemo(() => apiActionsToConnect(apiActions), [apiActions]);
  const filteredApiActionsToConnect = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q
      ? apiActionsNotConnected.filter((action) => action.name.toLowerCase().includes(q))
      : apiActionsNotConnected;
  }, [apiActionsNotConnected, search]);

  const handleActionConnected = useCallback(
    async (instanceId: string) => {
      // A Load more still running would turn this refresh away.
      while (isFetchingPageRef.current) await new Promise((resolve) => setTimeout(resolve, 100));
      const groups = await loadPage(1, { quiet: true });
      const connected = groups?.find((g) => g.instanceId === instanceId);
      // Someone who picked tools by hand gets the new action's on; "all tools" already has them.
      if (connected) setGroupToolsEnabled(connected.fullNames, true);
    },
    [loadPage, setGroupToolsEnabled]
  );

  const handleMcpConnected = useCallback(
    async (instanceId: string) => {
      const groups = await loadMcp({ quiet: true });
      const connected = groups?.find((g) => g.instanceId === instanceId);
      // Someone who picked tools by hand gets the new server's on; "all tools" already has them.
      if (connected) setGroupToolsEnabled(connected.fullNames, true);
    },
    [loadMcp, setGroupToolsEnabled]
  );

  const tabPlaceholders = [
    t('chat.agentResources.searchConnectors', { defaultValue: 'Search connectors' }),
    t('chat.agentResources.searchCollections', { defaultValue: 'Search collections' }),
    t('chat.agentResources.searchActions', { defaultValue: 'Search actions' }),
    t('chat.agentResources.searchMcp', { defaultValue: 'Search MCP servers' }),
  ];
  const tabIndex = TAB_VALUES.indexOf(tab);
  const searchPlaceholder = tabPlaceholders[tabIndex >= 0 ? tabIndex : 0];

  const tabLabels = useMemo(
    () => ({
      connectors: t('nav.connectors', { defaultValue: 'Connectors' }),
      collections: t('nav.collections', { defaultValue: 'Collections' }),
      actions: t('chat.agentResources.actionsTab', { defaultValue: 'Actions' }),
      mcp: t('chat.agentResources.mcpTab', { defaultValue: 'MCP' }),
    }),
    [t]
  );

  const toggleGroupExpanded = (key: string) => {
    setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleCollectionSelectionChange = useCallback(
    (next: CollectionScopeSelection) => {
      setFilters(next);
    },
    [setFilters]
  );

  /** Footer/empty-state "browse more" row shared by the Actions and MCP tabs. */
  const renderBrowseMoreRow = (opts: { href: string; label: string; ariaLabel: string }) => (
    <Flex
      align="center"
      justify="between"
      gap="2"
      style={{
        ...OLIVE_ROW,
        padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <MaterialIcon name="apps" size={18} color="var(--gray-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
          {opts.label}
        </Text>
      </Flex>
      <IconButton asChild size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
        <Link href={opts.href} aria-label={opts.ariaLabel}>
          <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
        </Link>
      </IconButton>
    </Flex>
  );

  /**
   * Shared tab body for Actions (toolsets) and MCP — identical loading/error/empty states and
   * group-row/checkbox/expand/footer-link structure, differing only in copy, footer link target,
   * icons, and optional pagination (`afterGroups`, used by Actions' "Load more").
   */
  const renderResourceGroupsTab = ({
    groups,
    keyPrefix,
    loading,
    error,
    hasAnyBeforeFilter,
    loadingText,
    noDataText,
    noMatchText,
    footerCaption,
    footerLinkHref,
    footerLinkText,
    footerLinkAriaLabel,
    renderGroupIcon,
    renderToolIcon,
    afterGroups,
    extraRows,
  }: {
    groups: CatalogToolGroupRow[];
    keyPrefix: string;
    loading: boolean;
    error: string | null;
    hasAnyBeforeFilter: boolean;
    loadingText: string;
    noDataText: string;
    noMatchText: string;
    footerCaption: string;
    footerLinkHref: string;
    footerLinkText: string;
    footerLinkAriaLabel: string;
    renderGroupIcon: (group: CatalogToolGroupRow) => React.ReactNode;
    renderToolIcon: (bareFn: string) => React.ReactNode;
    afterGroups?: React.ReactNode;
    /** Rows after the groups that aren't groups (the MCP servers still to connect). */
    extraRows?: React.ReactNode;
  }) => {
    if (loading) {
      return (
        <Flex align="center" justify="center" gap="2" style={{ padding: 'var(--space-4)' }}>
          <Spinner size="2" />
          <Text size="2" style={{ color: 'var(--gray-10)' }}>
            {loadingText}
          </Text>
        </Flex>
      );
    }

    if (error) {
      return (
        <Text size="2" style={{ color: 'var(--red-9)', padding: 'var(--space-3)' }}>
          {error}
        </Text>
      );
    }

    if (groups.length === 0 && !extraRows) {
      return (
        <Flex direction="column" gap="2" style={{ padding: 'var(--space-3)' }}>
          <Text size="2" style={{ color: 'var(--slate-9)' }}>
            {hasAnyBeforeFilter ? noMatchText : noDataText}
          </Text>
          {renderBrowseMoreRow({
            href: footerLinkHref,
            label: footerLinkText,
            ariaLabel: footerLinkAriaLabel,
          })}
        </Flex>
      );
    }

    return (
      <>
        {groups.map((group) => {
          const groupKey = `${keyPrefix}:${group.instanceId}`;
          const subtitle = toolsetSubtitle(group);
          const expanded = Boolean(expandedGroups[groupKey]);
          const checkState = groupCheckState(group.fullNames);

          return (
            <Flex key={groupKey} direction="column" gap="1">
              <Flex
                align="center"
                justify="between"
                gap="2"
                style={{
                  ...OLIVE_ROW,
                  padding: 'var(--space-2)',
                  cursor: 'pointer',
                }}
                onClick={() => toggleGroupExpanded(groupKey)}
              >
                <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
                  <div
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                    role="presentation"
                    style={CHECKBOX_ALIGN}
                  >
                    <Checkbox
                      size="1"
                      checked={checkState}
                      onCheckedChange={(v) => {
                        setGroupToolsEnabled(group.fullNames, v === true);
                      }}
                    />
                  </div>
                  {renderGroupIcon(group)}
                  <Flex align="center" gap="1" style={{ flex: 1, minWidth: 0, flexWrap: 'wrap' }}>
                    <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
                      {group.label}
                    </Text>
                    {subtitle ? (
                      <>
                        <Text size="2" weight="medium" style={{ color: 'var(--gray-10)' }}>
                          ·
                        </Text>
                        <Text size="2" weight="medium" style={{ color: 'var(--gray-10)' }} truncate>
                          {subtitle}
                        </Text>
                      </>
                    ) : null}
                  </Flex>
                </Flex>
                <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
                  <Badge size="1" variant="soft" color="green" highContrast>
                    {t('chat.agentResources.actionsCount', {
                      count: group.fullNames.length,
                    })}
                  </Badge>
                  <IconButton
                    type="button"
                    size="1"
                    variant="ghost"
                    color="gray"
                    aria-expanded={expanded}
                    aria-label={expanded ? t('common.collapse') : t('common.expand')}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleGroupExpanded(groupKey);
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
                </Flex>
              </Flex>

              {expanded &&
                group.fullNames.map((internalKey) => {
                  // Strip instanceId prefix for display and icon resolution
                  const fn = bareToolFullName(internalKey);
                  const shortRaw =
                    group.toolLabels?.[internalKey] ?? (fn.includes('.') ? fn.slice(fn.indexOf('.') + 1) : fn);
                  const short = humanizeUnderscores(shortRaw);
                  return (
                    <Flex
                      key={internalKey}
                      align="center"
                      gap="2"
                      onClick={() => toggleTool(internalKey)}
                      style={{
                        ...OLIVE_ROW,
                        marginLeft: 'var(--space-3)',
                        padding: 'var(--space-2) var(--space-3)',
                        cursor: 'pointer',
                      }}
                    >
                      <span style={CHECKBOX_ALIGN}>
                        <Checkbox
                          size="1"
                          checked={isToolOn(internalKey)}
                          onCheckedChange={() => toggleTool(internalKey)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </span>
                      {renderToolIcon(fn)}
                      <Flex direction="column" gap="0" style={{ flex: 1, minWidth: 0 }}>
                        <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
                          {short}
                        </Text>
                        <Text size="1" style={{ color: 'var(--gray-9)' }} truncate>
                          {fn}
                        </Text>
                      </Flex>
                    </Flex>
                  );
                })}
            </Flex>
          );
        })}

        {afterGroups}

        {extraRows}

        <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-1)' }}>
          <Text size="1" style={{ color: 'var(--gray-11)' }}>
            {footerCaption}
          </Text>
          {renderBrowseMoreRow({
            href: footerLinkHref,
            label: footerLinkText,
            ariaLabel: footerLinkAriaLabel,
          })}
        </Flex>
      </>
    );
  };

  return (
    <Flex
      direction="column"
      gap="3"
      style={{ flex: 1, minHeight: 0, height: '100%', overflow: 'hidden' }}
    >
      {/* Model gate warning — non-dismissable; send button is disabled externally */}
      {showModelGate && (
        <Callout.Root color="amber" size="1" style={{ flexShrink: 0 }}>
          <Callout.Icon>
            <MaterialIcon name="warning" size={16} color="var(--amber-11)" />
          </Callout.Icon>
          <Callout.Text>
            {t('chat.universalAgent.noReasoningModel', {
              defaultValue:
                'No reasoning-capable model is configured. Configure one in Settings → AI Models before using Agent mode.',
            })}
          </Callout.Text>
        </Callout.Root>
      )}

      {/* Header: tabs + expand/collapse toggle */}
      <Flex align="center" justify="between" gap="2" style={{ width: '100%', flexShrink: 0 }}>
        <UniversalAgentFilterTablist
          value={tab}
          onValueChange={(next) => {
            setTab(next);
            setSearch('');
          }}
          labels={tabLabels}
          disabledTabs={disabledTabs}
          hiddenTabs={hiddenTabs}
        />
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onToggleView}
          style={{
            cursor: onToggleView ? 'pointer' : 'default',
            flexShrink: 0,
            // Radix pulls ghost buttons out by their padding; the header has its own.
            margin: 0,
            padding: 0,
            width: 32,
            height: 32,
          }}
        >
          <MaterialIcon
            name={viewMode === 'overlay' ? 'close_fullscreen' : 'open_in_full'}
            size={16}
            color="var(--slate-9)"
          />
        </IconButton>
      </Flex>

      {/* Search (Actions + MCP tabs only) */}
      {(tab === 'actions' || tab === 'mcp') && (
        <Flex align="center" gap="2" style={{ width: '100%', flexShrink: 0 }}>
          <TextField.Root
            size="2"
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ flex: 1, minWidth: 0 }}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--gray-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Flex>
      )}

      {/* Tab content */}
      <Flex
        direction="column"
        gap="2"
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', width: '100%' }}
      >
        {/* Connectors + Collections share one CollectionsTab instance so the root list is
            fetched only once. Switching between the two tabs changes filterMode (a prop),
            which re-filters already-loaded data without any additional API call.
            The component only unmounts when the user switches to the Actions tab. */}
        {(tab === 'connectors' || tab === 'collections') && (
          <CollectionsTab
            selection={settings.filters}
            onSelectionChange={handleCollectionSelectionChange}
            filterMode={tab}
          />
        )}

        {/* Actions — from my-toolsets: connected actions' tools, then the ones to connect */}
        {tab === 'actions' &&
          renderResourceGroupsTab({
            groups: filteredToolGroups,
            keyPrefix: 'toolset',
            loading: toolsLoading,
            error: toolsError,
            hasAnyBeforeFilter:
              toolGroups.length > 0 || toolsetsToConnect.length > 0 || apiActionsNotConnected.length > 0,
            loadingText: t('chat.universalAgent.loadingTools', { defaultValue: 'Loading actions…' }),
            noDataText: t('chat.universalAgent.noActions', {
              defaultValue: 'No authenticated actions found. Connect integrations in Workspace → Actions.',
            }),
            noMatchText: t('chat.agentResources.noActionMatches', {
              defaultValue: 'No actions match your search.',
            }),
            footerCaption: t('chat.agentResources.configureMoreActions', {
              defaultValue: 'Configure more actions',
            }),
            footerLinkHref: '/workspace/actions',
            footerLinkText: t('chat.agentResources.browseWorkspaceActions', {
              defaultValue: 'Browse workspace actions',
            }),
            footerLinkAriaLabel: t('chat.agentResources.browseWorkspaceActionsAria', {
              defaultValue: 'Open workspace actions to add or manage integrations',
            }),
            renderGroupIcon: (group) => (
              <UniversalToolsetRowIcon
                toolsetSlug={group.toolsetSlug}
                label={group.label}
                iconPath={group.iconPath}
              />
            ),
            renderToolIcon: (fn) => {
              const toolPrefix = fn.includes('.') ? fn.slice(0, fn.indexOf('.')) : fn;
              return <ConnectorIcon type={resolveConnectorType(toolPrefix)} size={16} />;
            },
            afterGroups: hasNextPage ? (
              <Flex align="center" style={{ paddingTop: 'var(--space-1)' }}>
                <button
                  type="button"
                  onClick={handleLoadMore}
                  disabled={isLoadingMore}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: isLoadingMore ? 'default' : 'pointer',
                    color: 'var(--olive-9)',
                    fontSize: 12,
                    padding: 0,
                    textAlign: 'left',
                  }}
                >
                  {isLoadingMore
                    ? t('agentBuilder.loadingMore', { defaultValue: 'Loading more…' })
                    : t('agentBuilder.loadMore', { defaultValue: 'Load more' })}
                </button>
              </Flex>
            ) : null,
            extraRows:
              filteredToolsetsToConnect.length > 0 || filteredApiActionsToConnect.length > 0 ? (
                <>
                  <ToolsetConnectRows
                    toolsets={filteredToolsetsToConnect}
                    rowStyle={OLIVE_ROW}
                    renderIcon={(ts) => (
                      <UniversalToolsetRowIcon
                        toolsetSlug={ts.toolsetType ?? ts.name}
                        label={actionName(ts)}
                        iconPath={ts.iconPath || undefined}
                      />
                    )}
                    onConnected={(id) => void handleActionConnected(id)}
                  />
                  <ApiActionConnectRows
                    actions={filteredApiActionsToConnect}
                    isAdmin={isAdmin}
                    rowStyle={OLIVE_ROW}
                    showHeading={filteredToolsetsToConnect.length === 0}
                    onConnected={(id) => void handleActionConnected(id)}
                  />
                </>
              ) : undefined,
          })}

        {/* MCP — from my-mcp-servers: connected servers' tools, then the ones to connect */}
        {tab === 'mcp' &&
          renderResourceGroupsTab({
            groups: filteredMcpGroups,
            keyPrefix: 'mcp',
            loading: mcpLoading,
            error: mcpError,
            hasAnyBeforeFilter: mcpGroups.length > 0 || mcpToConnect.length > 0,
            loadingText: t('chat.universalAgent.loadingMcp', { defaultValue: 'Loading MCP servers…' }),
            noDataText: t('chat.universalAgent.noMcpServers', {
              defaultValue: 'No authenticated MCP servers found. Connect one in Workspace → MCP Servers.',
            }),
            noMatchText: t('chat.agentResources.noMcpMatches', {
              defaultValue: 'No MCP servers match your search.',
            }),
            footerCaption: t('chat.agentResources.configureMoreMcp', {
              defaultValue: 'Configure more MCP servers',
            }),
            footerLinkHref: '/workspace/mcp-servers/personal',
            footerLinkText: t('chat.agentResources.browseWorkspaceMcp', {
              defaultValue: 'Browse MCP servers',
            }),
            footerLinkAriaLabel: t('chat.agentResources.browseWorkspaceMcpAria', {
              defaultValue: 'Open MCP servers to add or manage connections',
            }),
            renderGroupIcon: () => <MaterialIcon name="hub" size={18} color="var(--gray-11)" />,
            renderToolIcon: () => <MaterialIcon name="hub" size={16} color="var(--gray-9)" />,
            extraRows:
              filteredMcpToConnect.length > 0 ? (
                <McpConnectRows
                  servers={filteredMcpToConnect}
                  rowStyle={OLIVE_ROW}
                  onConnected={(id) => void handleMcpConnected(id)}
                />
              ) : undefined,
          })}
      </Flex>

      {/* Footer */}
      <Flex align="center" justify="between" gap="2" style={{ flexShrink: 0 }}>
        <Button
          type="button"
          size="2"
          variant="outline"
          color="gray"
          onClick={resetToDefaults}
          style={{ height: 32, cursor: 'pointer' }}
        >
          <MaterialIcon name="restart_alt" size={14} color="var(--neutral-11)" />
          <Text size="2" weight="medium" style={{ color: 'var(--neutral-12)' }}>
            {t('chat.agentResources.resetDefaults', { defaultValue: 'Reset to defaults' })}
          </Text>
        </Button>
        {(tab === 'actions' || tab === 'mcp') && (
          <Flex align="center" gap="2">
            <Text size="1" style={{ color: 'var(--gray-9)' }}>
              {t('chat.universalAgent.toolCount', {
                count: selectedCount,
                defaultValue: `{{count}} tools`,
              })}
            </Text>
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}
