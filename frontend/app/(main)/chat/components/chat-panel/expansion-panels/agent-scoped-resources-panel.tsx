'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from '@/lib/navigation';
import Image from 'next/image';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  Flex,
  IconButton,
  Text,
  TextField,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { useChatStore, type ScopedRowSource } from '@/chat/store';
import { bareToolFullName } from '@/chat/tool-groups';
import { McpServerIcon } from '@/app/(main)/workspace/mcp-servers/components';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectActionsEnabled,
} from '@/lib/store/feature-flags-store';
import { KB_MIN_SEARCH_QUERY_LENGTH } from '@/app/(main)/knowledge-base/utils';
import { useDebouncedSearch } from '@/app/(main)/knowledge-base/hooks/use-debounced-search';
import {
  KnowledgeSearchResults,
  KnowledgeTreeGroup,
  KnowledgeTreeRoot,
  type KnowledgeTreeNode,
} from './connectors-collections/knowledge-tree';
import { CollectionLeadingIcon } from './connectors-collections/collection-row';
import { hasAnyFilter, sameSelection } from '@/chat/utils/tree-selection';
import type { ChatKnowledgeFilters } from '@/chat/types';

type ExpansionViewMode = 'inline' | 'overlay';

const TAB_VALUES = ['connectors', 'collections', 'actions', 'mcp'] as const;
type TabValue = (typeof TAB_VALUES)[number];

/**
 * Figma segmented control track (`7523:16159` / Settings spec): 32px height, 4px radius.
 * Width was a fixed 246px sized for 3 tabs — scaled per-tab so a 4th ("MCP") entry doesn't
 * crush the longer "Connectors"/"Collections" labels. Computed from the actually-rendered
 * tab count (`visibleTabValues`) so hiding the MCP tab shrinks the tablist back down.
 */
const FIGMA_TABLIST_WIDTH_PER_TAB = 82;
const FIGMA_TABLIST_HEIGHT = 32;
const FIGMA_TABLIST_RADIUS = 4;

/**
 * Segmented tabs matching Figma (node `7523:16159` + dark CSS from spec):
 * — Track: layered gradients + base tint; light uses MCP tokens, dark uses spec rgba.
 * — Items: 12px type, 0.04px tracking, 16px line-height; inactive regular, selected medium.
 * — Selected: light = page surface + neutral hairline; dark = `#111113` + frosted border.
 */
function AgentFilterTablist({
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
      aria-label="Chat filters"
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

/** Figma: olive-2 surface, olive-3 hairline border, 3px radius */
const OLIVE_ROW = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-1-max, 3px)',
} as const;

/** Centers Radix checkbox with row icons/text (checkbox defaults sit high on the line box). */
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

/** Prefer graph `type`/`name`, but merge `label` so UUID-only rows still fuzzy-match (e.g. Jira). */
function connectorIconHint(row: { connectorKind?: string; label: string }): string {
  return `${row.connectorKind ?? ''} ${row.label}`.trim();
}

/** Stable fallback so the Zustand selector always returns the same reference when no scoped caps exist. */
const DEFAULT_AGENT_CAPS = { internalSearch: true, webSearch: true } as const;
const EMPTY_STRINGS: string[] = [];
const EMPTY_ROWS: never[] = [];
const EMPTY_KNOWLEDGE = { apps: EMPTY_STRINGS, kb: EMPTY_STRINGS };

/** Which store slice the panel renders — a custom agent (`?agentId=`) or a project (`?projectId=`). */
export type ScopedResourcesSource = 'agent' | 'project';

interface AgentScopedResourcesPanelProps {
  onToggleView?: () => void;
  viewMode?: ExpansionViewMode;
  scope?: ScopedResourcesSource;
}

/**
 * Selects the agent or project slice of the chat store behind one shape so the panel body
 * doesn't branch on the source. Both slices share semantics: `knowledgeScope === null` and
 * `selectedTools === null` mean "everything in defaults / catalog".
 */
function useScopedResourceSource(scope: ScopedResourcesSource) {
  const isProject = scope === 'project';

  const agentConnectors = useChatStore((s) => s.agentChatConnectors);
  const agentCollectionRows = useChatStore((s) => s.agentKnowledgeCollectionRows);
  const agentToolGroups = useChatStore((s) => s.agentChatToolGroups);
  const agentMcpGroups = useChatStore((s) => s.agentChatMcpGroups);
  const agentDefaults = useChatStore((s) => s.agentKnowledgeDefaults);
  const agentKnowledgeScope = useChatStore((s) => s.agentKnowledgeScope);
  const agentSelectedTools = useChatStore((s) => s.agentStreamTools);
  const setAgentKnowledgeScope = useChatStore((s) => s.setAgentKnowledgeScope);
  const setAgentStreamTools = useChatStore((s) => s.setAgentStreamTools);
  const agentId = useChatStore((s) => s.agentSidebarAgentId);
  const scopedCaps = useChatStore((s) =>
    agentId ? (s.scopedAgentCapabilities[agentId] ?? DEFAULT_AGENT_CAPS) : DEFAULT_AGENT_CAPS
  );

  const projectScope = useChatStore((s) => s.projectScope);
  const projectKnowledgeScope = useChatStore((s) => s.projectKnowledgeScope);
  const projectSelectedTools = useChatStore((s) => s.projectStreamTools);
  const setProjectKnowledgeScope = useChatStore((s) => s.setProjectKnowledgeScope);
  const setProjectStreamTools = useChatStore((s) => s.setProjectStreamTools);

  const getProjectToolCatalog = useCallback(
    () => useChatStore.getState().projectScope?.toolCatalogFullNames ?? EMPTY_STRINGS,
    []
  );
  const getAgentToolCatalog = useCallback(() => useChatStore.getState().agentToolCatalogFullNames, []);

  if (isProject) {
    const defaults = projectScope?.knowledgeDefaults ?? EMPTY_KNOWLEDGE;
    return {
      connectors: projectScope?.connectors ?? EMPTY_ROWS,
      collectionRows: projectScope?.knowledgeCollectionRows ?? EMPTY_ROWS,
      toolGroups: projectScope?.toolGroups ?? EMPTY_ROWS,
      mcpGroups: projectScope?.mcpGroups ?? EMPTY_ROWS,
      defaults,
      knowledgeScope: projectKnowledgeScope,
      selectedTools: projectSelectedTools,
      setKnowledgeScope: setProjectKnowledgeScope,
      setTools: setProjectStreamTools,
      getToolCatalog: getProjectToolCatalog,
      internalSearchEnabled: hasAnyFilter(defaults),
    };
  }

  const agentHasInternalSearch = hasAnyFilter(agentDefaults);
  return {
    connectors: agentConnectors,
    collectionRows: agentCollectionRows,
    toolGroups: agentToolGroups,
    mcpGroups: agentMcpGroups,
    defaults: agentDefaults,
    knowledgeScope: agentKnowledgeScope,
    selectedTools: agentSelectedTools,
    setKnowledgeScope: setAgentKnowledgeScope,
    setTools: setAgentStreamTools,
    getToolCatalog: getAgentToolCatalog,
    internalSearchEnabled: agentHasInternalSearch ? scopedCaps.internalSearch : false,
  };
}

function setsEqualAsSets(a: string[], b: string[]): boolean {
  if (a === b) return true;
  const sa = new Set(a);
  const sb = new Set(b);
  if (sa.size !== sb.size) return false;
  return Array.from(sa).every((x) => sb.has(x));
}

function effectiveKnowledge(
  scope: ChatKnowledgeFilters | null,
  defaults: ChatKnowledgeFilters
): ChatKnowledgeFilters {
  return scope ?? defaults;
}

function toolsetSubtitle(group: { fullNames: string[] }): string {
  const fn = group.fullNames[0];
  if (!fn || !fn.includes('.')) return '';
  return fn
    .slice(0, fn.indexOf('.'))
    .replace(/_/g, ' ')
    .trim();
}

function AgentToolsetRowIcon({
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

/** Rows in their listed order; the nodes one source is limited to come together, under it. */
interface SourceGroup<T> {
  source: ScopedRowSource | null;
  rows: T[];
}

function groupBySource<T extends { source?: ScopedRowSource }>(rows: T[]): SourceGroup<T>[] {
  const groups: SourceGroup<T>[] = [];
  const bySource = new Map<string, SourceGroup<T>>();
  for (const row of rows) {
    if (!row.source) {
      groups.push({ source: null, rows: [row] });
      continue;
    }
    let group = bySource.get(row.source.id);
    if (!group) {
      group = { source: row.source, rows: [] };
      bySource.set(row.source.id, group);
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}

function isCollectionKind(kind: string): boolean {
  return kind.trim().toUpperCase() === 'KB';
}

/** A project knows only a source's type, so its heading falls back to the type's name. */
function sourceLabel(source: ScopedRowSource, collectionsLabel: string): string {
  return source.label || (isCollectionKind(source.kind) ? collectionsLabel : humanizeUnderscores(source.kind));
}

/** What a search matches a row's heading on: what the heading shows, and the source's type. */
function sourceSearchText(source: ScopedRowSource | undefined, collectionsLabel: string): string {
  if (!source) return '';
  return `${sourceLabel(source, collectionsLabel)} ${source.kind} ${humanizeUnderscores(source.kind)}`.toLowerCase();
}

function LimitedSourceIcon({ source }: { source: ScopedRowSource }) {
  return isCollectionKind(source.kind) ? (
    <CollectionLeadingIcon sourceType="KB" size={20} />
  ) : (
    <ConnectorIcon type={resolveConnectorType(`${source.kind} ${source.label}`.trim())} size={20} />
  );
}

/** Row shape shared by `agentChatToolGroups` (Actions tab) and `agentChatMcpGroups` (MCP tab). */
interface ChatToolGroupRow {
  label: string;
  fullNames: string[];
  toolDescriptions?: Record<string, string>;
  /** Display name per `fullNames` entry, where the full name doesn't read as one (MCP, API actions). */
  toolLabels?: Record<string, string>;
  toolsetSlug: string;
  instanceId?: string;
  iconPath?: string;
  /** An MCP server's catalog type, which picks its logo. */
  typeId?: string;
}

/**
 * Connectors · Collections · Actions · MCP panel for agent-scoped chat (`?agentId=`).
 * Matches Settings spec (Figma `7523:16157` — segmented tabs, search row, actions summary rows, configure-more links).
 */
export function AgentScopedResourcesPanel({
  onToggleView,
  viewMode = 'inline',
  scope: source = 'agent',
}: AgentScopedResourcesPanelProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabValue>('connectors');
  const [search, setSearch] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const actionsEnabled = useFeatureFlagsStore(selectActionsEnabled);
  const isProject = source === 'project';
  // Project chats run in plain chat mode too; tools only ship in agent mode, so hide those tabs.
  const projectToolsApplicable = useChatStore((s) => !isProject || s.settings.queryMode === 'agent');

  const {
    connectors,
    collectionRows,
    toolGroups,
    mcpGroups,
    defaults,
    knowledgeScope: scope,
    selectedTools,
    setKnowledgeScope: setScope,
    setTools,
    getToolCatalog,
    internalSearchEnabled,
  } = useScopedResourceSource(source);

  const copy = useMemo(
    () =>
      isProject
        ? {
            noConnectors: t('chat.projectResources.noConnectors', {
              defaultValue: 'No connectors enabled for this project.',
            }),
            noCollections: t('chat.projectResources.noCollections', {
              defaultValue: 'No collections enabled for this project.',
            }),
            noActions: t('chat.projectResources.noActions', {
              defaultValue: 'No actions (toolsets) enabled for this project.',
            }),
            noMcpServers: t('chat.projectResources.noMcpServers', {
              defaultValue: 'No MCP servers enabled for this project.',
            }),
            resetDefaults: t('chat.projectResources.resetDefaults', {
              defaultValue: 'Reset to project defaults',
            }),
          }
        : {
            noConnectors: t('chat.agentResources.noConnectors', {
              defaultValue: 'No connectors configured for this agent.',
            }),
            noCollections: t('chat.agentResources.noCollections', {
              defaultValue: 'No collections configured for this agent.',
            }),
            noActions: t('chat.agentResources.noActions', {
              defaultValue: 'No actions (toolsets) configured for this agent.',
            }),
            noMcpServers: t('chat.agentResources.noMcpServers', {
              defaultValue: 'No MCP servers configured for this agent.',
            }),
            resetDefaults: t('chat.agentResources.resetDefaults', { defaultValue: 'Reset to defaults' }),
          },
    [isProject, t]
  );

  const eff = useMemo(() => effectiveKnowledge(scope, defaults), [scope, defaults]);

  /**
   * User turned off every configured knowledge source (defaults still list agent knowledge).
   * Omit when the agent never had knowledge (tools-only / retrieval-less agents).
   */
  const showKnowledgeClearedWarning = useMemo(
    () => !hasAnyFilter(eff) && hasAnyFilter(defaults),
    [eff, defaults]
  );

  /** A selection equal to the defaults is stored as "nothing narrowed". */
  const selectKnowledge = useCallback(
    (next: ChatKnowledgeFilters) => setScope(sameSelection(next, defaults) ? null : next),
    [defaults, setScope]
  );

  const isToolOn = useCallback(
    (fullName: string) => {
      if (selectedTools === null) return true;
      return selectedTools.includes(fullName);
    },
    [selectedTools]
  );

  const toggleTool = useCallback(
    (fullName: string) => {
      const cat = getToolCatalog();
      const cur = selectedTools;

      if (cur === null) {
        const next = cat.filter((x) => x !== fullName);
        if (next.length === 0) {
          setTools([]);
        } else if (cat.length > 0 && setsEqualAsSets(next, cat)) {
          setTools(null);
        } else {
          setTools(next);
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
        setTools([]);
        return;
      }
      if (cat.length > 0 && setsEqualAsSets(next, cat)) {
        setTools(null);
      } else {
        setTools(next);
      }
    },
    [selectedTools, getToolCatalog, setTools]
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
      const cat = getToolCatalog();
      const explicit = selectedTools;

      if (explicit === null) {
        if (!enabled) {
          const next = cat.filter((fn) => !fullNames.includes(fn));
          if (next.length === 0) {
            setTools([]);
          } else if (cat.length > 0 && setsEqualAsSets(next, cat)) {
            setTools(null);
          } else {
            setTools(next);
          }
        }
        return;
      }

      const nextSet = new Set(explicit);
      if (enabled) {
        for (const fn of fullNames) nextSet.add(fn);
      } else {
        for (const fn of fullNames) nextSet.delete(fn);
      }
      const arr = Array.from(nextSet);
      if (arr.length === 0) {
        setTools([]);
        return;
      }
      if (cat.length > 0 && setsEqualAsSets(arr, cat)) {
        setTools(null);
      } else {
        setTools(arr);
      }
    },
    [selectedTools, getToolCatalog, setTools]
  );

  const resetToAgentDefaults = useCallback(() => {
    setScope(null);
    setTools(null);
  }, [setScope, setTools]);

  const disabledTabs = useMemo<TabValue[]>(
    () => (!internalSearchEnabled ? ['connectors', 'collections'] : []),
    [internalSearchEnabled]
  );
  const hiddenTabs = useMemo<TabValue[]>(
    () => [
      ...(mcpEnabled && projectToolsApplicable ? [] : (['mcp'] as TabValue[])),
      ...(actionsEnabled && projectToolsApplicable ? [] : (['actions'] as TabValue[])),
    ],
    [mcpEnabled, actionsEnabled, projectToolsApplicable]
  );

  useEffect(() => {
    if (disabledTabs.includes(tab) || hiddenTabs.includes(tab)) {
      const fallback = TAB_VALUES.find((v) => !disabledTabs.includes(v) && !hiddenTabs.includes(v));
      if (fallback) setTab(fallback);
    }
  }, [disabledTabs, hiddenTabs, tab]);

  // A row matches on its own name or on the heading it is listed under.
  const collectionsLabel = t('nav.collections', { defaultValue: 'Collections' });
  const filteredConnectors = useMemo(() => {
    const trimmed = search.trim();
    if (trimmed.length > 0 && trimmed.length < KB_MIN_SEARCH_QUERY_LENGTH) return [];
    if (!trimmed) return connectors;
    const q = trimmed.toLowerCase();
    return connectors.filter(
      (c) =>
        c.label.toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q) ||
        sourceSearchText(c.source, collectionsLabel).includes(q)
    );
  }, [connectors, search, collectionsLabel]);

  const filteredCollections = useMemo(() => {
    const trimmed = search.trim();
    if (trimmed.length > 0 && trimmed.length < KB_MIN_SEARCH_QUERY_LENGTH) return [];
    if (!trimmed) return collectionRows;
    const q = trimmed.toLowerCase();
    return collectionRows.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q) ||
        sourceSearchText(r.source, collectionsLabel).includes(q)
    );
  }, [collectionRows, search, collectionsLabel]);

  // What lies under the listed sources is searched on the server.
  const serverQuery = useDebouncedSearch(search.trim(), 300);
  const knowledgeTabRows = tab === 'collections' ? collectionRows : tab === 'connectors' ? connectors : EMPTY_ROWS;
  const searchRoots = useMemo(
    () => knowledgeTabRows.map((row) => ({ id: row.id, nodeType: row.nodeType ?? 'app' })),
    [knowledgeTabRows]
  );
  const listedIds = useMemo(() => new Set(knowledgeTabRows.map((row) => row.id)), [knowledgeTabRows]);
  const knowledgeTab = tab === 'connectors' || tab === 'collections';
  // From the second character, also while the debounce runs, so "nothing found" does not flash first.
  const searchingServer = knowledgeTab && search.trim().length >= 2;

  // A heading counts what its source is limited to, not what the search left of it.
  const limitedCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of [...connectors, ...collectionRows]) {
      if (row.source) counts.set(row.source.id, (counts.get(row.source.id) ?? 0) + 1);
    }
    return counts;
  }, [connectors, collectionRows]);

  const renderSourceGroups = <T extends { source?: ScopedRowSource }>(
    rows: T[],
    toNode: (row: T) => KnowledgeTreeNode
  ) =>
    groupBySource(rows).flatMap((group) => {
      if (!group.source) {
        return group.rows.map((row) => {
          const node = toNode(row);
          return <KnowledgeTreeRoot key={node.id} node={node} filters={eff} onSelectionChange={selectKnowledge} />;
        });
      }
      return [
        <KnowledgeTreeGroup
          key={`source:${group.source.id}`}
          title={sourceLabel(group.source, collectionsLabel)}
          icon={<LimitedSourceIcon source={group.source} />}
          meta={t('chat.agentResources.limitedToCount', {
            count: limitedCounts.get(group.source.id) ?? group.rows.length,
          })}
          nodes={group.rows.map(toNode)}
          filters={eff}
          onSelectionChange={selectKnowledge}
        />,
      ];
    });

  const filterGroupsBySearch = useCallback(
    (groups: ChatToolGroupRow[]): ChatToolGroupRow[] =>
      groups
        .map((g) => ({
          ...g,
          fullNames: g.fullNames.filter((fn) => {
            if (!search.trim()) return true;
            const q = search.toLowerCase();
            // The row shows `toolLabels` (an API action's operation summary), so search it too.
            const desc = `${g.toolDescriptions?.[fn] ?? ''} ${g.toolLabels?.[fn] ?? ''}`.toLowerCase();
            return (
              fn.toLowerCase().includes(q) ||
              g.label.toLowerCase().includes(q) ||
              desc.includes(q)
            );
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

  /**
   * Shared row list for the Actions (toolsets) and MCP tabs — identical group-row/checkbox/
   * expand/footer-link structure, differing only in the empty-state copy, footer link target,
   * and group icon. `keyPrefix` namespaces `expandedGroups`/React keys so the two tabs never
   * collide on the same group key (e.g. two instanceIds that happen to match).
   */
  const renderToolGroupList = ({
    groups,
    keyPrefix,
    emptyText,
    footerCaption,
    footerLinkHref,
    footerLinkText,
    footerLinkAriaLabel,
    renderGroupIcon,
    renderToolIcon,
  }: {
    groups: ChatToolGroupRow[];
    keyPrefix: string;
    emptyText: string;
    /** Small caption above the footer row (e.g. "Configure more actions"). */
    footerCaption: string;
    footerLinkHref: string;
    /** Label inside the footer row itself (e.g. "Browse workspace actions"). */
    footerLinkText: string;
    footerLinkAriaLabel: string;
    renderGroupIcon: (group: ChatToolGroupRow) => React.ReactNode;
    renderToolIcon: (fullName: string, group: ChatToolGroupRow) => React.ReactNode;
  }) => {
    if (groups.length === 0) {
      return (
        <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
          {emptyText}
        </Text>
      );
    }

    return (
      <>
        {groups.map((group) => {
          const groupKey = group.instanceId
            ? `${keyPrefix}:${group.instanceId}`
            : `${keyPrefix}:${group.toolsetSlug || 'toolset'}:${group.label}:${group.fullNames[0] ?? ''}`;
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
                group.fullNames.map((fullName) => {
                  // Project keys carry an instance prefix; show the name the server knows.
                  const fn = bareToolFullName(fullName);
                  const shortRaw =
                    group.toolLabels?.[fullName] ?? (fn.includes('.') ? fn.slice(fn.indexOf('.') + 1) : fn);
                  const short = humanizeUnderscores(shortRaw);
                  return (
                    <Flex
                      key={fullName}
                      align="center"
                      gap="2"
                      onClick={() => toggleTool(fullName)}
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
                          checked={isToolOn(fullName)}
                          onCheckedChange={() => toggleTool(fullName)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </span>
                      {renderToolIcon(fn, group)}
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

        <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-1)' }}>
          <Text size="1" style={{ color: 'var(--gray-11)' }}>
            {footerCaption}
          </Text>
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
                {footerLinkText}
              </Text>
            </Flex>
            <IconButton asChild size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
              <Link href={footerLinkHref} aria-label={footerLinkAriaLabel}>
                <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
              </Link>
            </IconButton>
          </Flex>
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
      <Flex align="center" justify="between" gap="2" style={{ width: '100%', flexShrink: 0 }}>
        <AgentFilterTablist
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

      {(tab === 'connectors' || tab === 'collections') && showKnowledgeClearedWarning && (
        <Callout.Root color="amber" size="1" style={{ flexShrink: 0 }}>
          <Callout.Icon>
            <MaterialIcon name="warning" size={16} color="var(--amber-11)" />
          </Callout.Icon>
          <Callout.Text>
            {t('chat.agentResources.knowledgeScopeClearedWarning')}
          </Callout.Text>
        </Callout.Root>
      )}

      <Flex
        direction="column"
        gap="2"
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', width: '100%' }}
      >
        {tab === 'connectors' && (
          <>
            {connectors.length === 0 ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {copy.noConnectors}
              </Text>
            ) : filteredConnectors.length === 0 && !searchingServer ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {search.trim().length < KB_MIN_SEARCH_QUERY_LENGTH
                  ? t('chat.nodeSearchTooShort', { defaultValue: 'Type at least 2 characters' })
                  : t('chat.noConnectorsFound')}
              </Text>
            ) : (
              renderSourceGroups(filteredConnectors, (c) => ({
                id: c.id,
                name: c.label,
                nodeType: c.nodeType ?? 'app',
                connector: connectorIconHint(c),
                hasChildren: true,
                ancestorIds: [],
              }))
            )}
          </>
        )}

        {tab === 'collections' && (
          <>
            {collectionRows.length === 0 ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {copy.noCollections}
              </Text>
            ) : filteredCollections.length === 0 && !searchingServer ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {search.trim().length < KB_MIN_SEARCH_QUERY_LENGTH
                  ? t('chat.nodeSearchTooShort', { defaultValue: 'Type at least 2 characters' })
                  : t('chat.agentResources.noCollectionMatches', {
                      defaultValue: 'No collections match your search.',
                    })}
              </Text>
            ) : (
              renderSourceGroups(filteredCollections, (row) => ({
                id: row.id,
                name: row.name,
                nodeType: row.nodeType ?? 'app',
                connector: row.sourceType ?? 'KB',
                hasChildren: true,
                ancestorIds: [],
                // A whole collection is kept apart from the apps here.
                ...(row.nodeType ? {} : { bucket: 'kb' as const }),
              }))
            )}
          </>
        )}

        {knowledgeTab && serverQuery && knowledgeTabRows.length > 0 ? (
          <KnowledgeSearchResults
            query={serverQuery}
            scope={{ roots: searchRoots }}
            excludeIds={listedIds}
            emptyText={
              (tab === 'connectors' ? filteredConnectors.length : filteredCollections.length) === 0
                ? tab === 'connectors'
                  ? t('chat.noConnectorsFound')
                  : t('chat.agentResources.noCollectionMatches', { defaultValue: 'No collections match your search.' })
                : null
            }
            filters={eff}
            onSelectionChange={selectKnowledge}
          />
        ) : null}

        {tab === 'actions' &&
          renderToolGroupList({
            groups: filteredToolGroups,
            keyPrefix: 'toolset',
            emptyText: copy.noActions,
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
              <AgentToolsetRowIcon
                toolsetSlug={group.toolsetSlug}
                label={group.label}
                iconPath={group.iconPath}
              />
            ),
            renderToolIcon: (fullName) => {
              const toolPrefix = fullName.includes('.') ? fullName.slice(0, fullName.indexOf('.')) : fullName;
              return <ConnectorIcon type={resolveConnectorType(toolPrefix)} size={16} />;
            },
          })}

        {tab === 'mcp' &&
          renderToolGroupList({
            groups: filteredMcpGroups,
            keyPrefix: 'mcp',
            emptyText: copy.noMcpServers,
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
            renderGroupIcon: (group) => <McpServerIcon typeId={group.typeId} name={group.label} size={18} />,
            renderToolIcon: (_fullName, group) => (
              <McpServerIcon typeId={group.typeId} name={group.label} size={16} color="var(--gray-9)" />
            ),
          })}
      </Flex>

      <Flex align="center" justify="between" gap="2" style={{ flexShrink: 0 }}>
        <Button type="button" size="1" variant="outline" color="gray" onClick={resetToAgentDefaults}>
          {copy.resetDefaults}
        </Button>
      </Flex>
    </Flex>
  );
}
