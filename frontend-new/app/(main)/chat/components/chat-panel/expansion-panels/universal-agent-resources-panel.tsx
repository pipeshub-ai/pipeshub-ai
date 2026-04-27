'use client';

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import Link from 'next/link';
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
import { useChatStore, ASSISTANT_CTX } from '@/chat/store';
import { ToolsetsApi } from '@/app/(main)/toolsets/api';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { CollectionsTab } from './connectors-collections/collections-tab';
import type { CollectionScopeSelection } from './connectors-collections/collections-tab';

type ExpansionViewMode = 'inline' | 'overlay';

const TAB_VALUES = ['connectors', 'collections', 'actions'] as const;
type TabValue = (typeof TAB_VALUES)[number];

const FIGMA_TABLIST_WIDTH = 246;
const FIGMA_TABLIST_HEIGHT = 32;
const FIGMA_TABLIST_RADIUS = 4;

function UniversalAgentFilterTablist({
  value,
  onValueChange,
  labels,
}: {
  value: TabValue;
  onValueChange: (next: TabValue) => void;
  labels: Record<TabValue, string>;
}) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';

  const trackStyle: React.CSSProperties = {
    width: FIGMA_TABLIST_WIDTH,
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
    const i = TAB_VALUES.indexOf(value);
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      onValueChange(TAB_VALUES[(i + 1) % TAB_VALUES.length]!);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      onValueChange(TAB_VALUES[(i - 1 + TAB_VALUES.length) % TAB_VALUES.length]!);
    }
  };

  return (
    <div
      role="tablist"
      aria-label="Universal agent filters"
      onKeyDown={onKeyDown}
      style={trackStyle}
    >
      {TAB_VALUES.map((tabValue) => {
        const selected = value === tabValue;
        return (
          <button
            key={tabValue}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onValueChange(tabValue)}
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
              color: inactiveColor,
              fontSize: 12,
              lineHeight: '16px',
              letterSpacing: '0.04px',
              fontWeight: selected ? 500 : 400,
              fontFamily: 'inherit',
              cursor: 'pointer',
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

/**
 * Strip the `instanceId:` prefix from an internal key to get the bare fullName.
 * Internal keys are `${instanceId}:${fullName}` when the toolset has an instanceId,
 * or just `fullName` for toolsets without one.
 */
function bareFullName(key: string): string {
  const colon = key.indexOf(':');
  return colon >= 0 ? key.slice(colon + 1) : key;
}

function toolsetSubtitle(group: { fullNames: string[] }): string {
  const raw = group.fullNames[0];
  if (!raw) return '';
  const fn = bareFullName(raw);
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

/**
 * Build tool groups from authenticated my-toolsets.
 *
 * **Internal key format:** when a toolset has an `instanceId`, each entry in `fullNames`
 * is stored as `${instanceId}:${rawFullName}`. This ensures two instances of the same
 * toolset type (e.g. two Slack workspaces sharing identical tool names) are independently
 * selectable in the UI. The prefix is stripped before sending on the wire (see runtime.ts).
 * For toolsets without an `instanceId`, the raw fullName is used unchanged.
 */
function buildUniversalToolGroups(toolsets: BuilderSidebarToolset[]): Array<{
  label: string;
  fullNames: string[];
  toolDescriptions?: Record<string, string>;
  toolsetSlug: string;
  instanceId: string;
  iconPath?: string;
  isAuthenticated: boolean;
}> {
  const groups: ReturnType<typeof buildUniversalToolGroups> = [];
  for (let i = 0; i < toolsets.length; i++) {
    const ts = toolsets[i]!;
    const rawInstanceId = typeof ts.instanceId === 'string' ? ts.instanceId.trim() : '';

    // Always assign a unique discriminator per entry in the list.
    // Prefer the real instanceId from the API (stable, meaningful).
    // Fall back to the loop index so that multiple instances of the same toolset type
    // (e.g., three Gmail workspaces) never share internal selection keys even when
    // the API omits instanceId.
    const groupDiscriminator = rawInstanceId || `local-${i}`;

    const rawFullNames = (ts.tools || [])
      .map((t) => (typeof t.fullName === 'string' ? t.fullName.trim() : ''))
      .filter(Boolean);
    if (rawFullNames.length === 0) continue;

    // Every internal key is always prefixed — guarantees uniqueness in the store.
    const fullNames = rawFullNames.map((fn) => `${groupDiscriminator}:${fn}`);

    const toolDescriptions: Record<string, string> = {};
    rawFullNames.forEach((rawFn, j) => {
      const key = fullNames[j]!;
      const t = (ts.tools || [])[j];
      const d = t && typeof t.description === 'string' ? t.description.trim() : '';
      if (d) toolDescriptions[key] = d;
    });

    const instanceLabel = typeof ts.instanceName === 'string' ? ts.instanceName.trim() : '';
    const productLabel = (ts.displayName || ts.name || 'Tools').trim();

    groups.push({
      label: instanceLabel || productLabel,
      toolsetSlug: (ts.toolsetType || ts.name || '').trim(),
      instanceId: groupDiscriminator,
      iconPath: ts.iconPath?.trim() || undefined,
      fullNames,
      toolDescriptions: Object.keys(toolDescriptions).length ? toolDescriptions : undefined,
      isAuthenticated: Boolean(ts.isAuthenticated),
    });
  }
  return groups;
}

interface UniversalAgentResourcesPanelProps {
  onToggleView?: () => void;
  viewMode?: ExpansionViewMode;
}

/**
 * Connectors · Collections · Actions panel for universal agent mode
 * (main chat, `queryMode === 'agent'`, no `agentId` in URL).
 *
 * - Connectors / Collections: delegate to CollectionsTab (same data path as assistant).
 * - Actions: loads from GET /api/v1/toolsets/my-toolsets (authenticated instances only).
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

  const settings = useChatStore((s) => s.settings);
  const setFilters = useChatStore((s) => s.setFilters);

  const toolGroups = useChatStore((s) => s.universalAgentToolGroups);
  const toolCatalog = useChatStore((s) => s.universalAgentToolCatalogFullNames);
  const selectedTools = useChatStore((s) => s.universalAgentStreamTools);
  const toolsLoading = useChatStore((s) => s.universalAgentToolsLoading);
  const toolsError = useChatStore((s) => s.universalAgentToolsError);
  const setSelectedTools = useChatStore((s) => s.setUniversalAgentStreamTools);
  const hydrateResources = useChatStore((s) => s.hydrateUniversalAgentResources);
  const setToolsLoading = useChatStore((s) => s.setUniversalAgentToolsLoading);
  const setToolsError = useChatStore((s) => s.setUniversalAgentToolsError);

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

  // ── Client-side tool cap ──
  // Backend enforces MAX_TOOLS_LIMIT = 128 (internal tools counted first).
  // We expose a conservative client-side cap so the user sees a count before hitting
  // the server guard. Internal tools are unknown on the client, so we cap at the full
  // server limit and let the backend truncate if needed.
  const MAX_USER_TOOLS = 128;
  const selectedCount = useMemo(() => {
    if (selectedTools === null) return toolCatalog.length;
    return selectedTools.length;
  }, [selectedTools, toolCatalog.length]);
  const atToolCap = selectedCount >= MAX_USER_TOOLS;

  // ── Load toolsets on first mount (or when panel mounts fresh) ──
  const hasFetchedRef = useRef(false);

  useEffect(() => {
    if (hasFetchedRef.current) return;
    // Only fetch if not already loaded (avoid refetch every panel open)
    if (toolGroups.length > 0 || toolsLoading) return;
    hasFetchedRef.current = true;

    setToolsLoading(true);
    ToolsetsApi.getAllMyToolsets({ authStatus: 'authenticated', limitPerPage: 50 })
      .then(({ toolsets }) => {
        const groups = buildUniversalToolGroups(toolsets);
        const catalogFullNames = groups.flatMap((g) => g.fullNames);
        hydrateResources({ toolGroups: groups, toolCatalogFullNames: catalogFullNames });
      })
      .catch((err) => {
        console.error('[UniversalAgentResourcesPanel] Failed to load my-toolsets', err);
        setToolsError(
          t('chat.universalAgent.toolsLoadError', {
            defaultValue: 'Failed to load available actions.',
          })
        );
        setToolsLoading(false);
      });
  }, [toolGroups.length, toolsLoading, hydrateResources, setToolsLoading, setToolsError, t]);

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
      const cat = useChatStore.getState().universalAgentToolCatalogFullNames;
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
        // Enforce client-side cap before adding
        if (next.length >= MAX_USER_TOOLS) return;
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
    [setSelectedTools, MAX_USER_TOOLS]
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
      const cat = useChatStore.getState().universalAgentToolCatalogFullNames;
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
          if (nextSet.size >= MAX_USER_TOOLS) break; // enforce cap
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
  }, [setSelectedTools]);

  // ── Filtered views ──

  const filteredToolGroups = useMemo(() => {
    return toolGroups
      .map((g) => ({
        ...g,
        fullNames: g.fullNames.filter((key) => {
          if (!search.trim()) return true;
          const q = search.toLowerCase();
          const desc = (g.toolDescriptions?.[key] ?? '').toLowerCase();
          // Strip the instanceId prefix when comparing against the search term
          const bare = bareFullName(key);
          return bare.toLowerCase().includes(q) || g.label.toLowerCase().includes(q) || desc.includes(q);
        }),
      }))
      .filter((g) => g.fullNames.length > 0);
  }, [toolGroups, search]);

  const tabPlaceholders = [
    t('chat.agentResources.searchConnectors', { defaultValue: 'Search connectors' }),
    t('chat.agentResources.searchCollections', { defaultValue: 'Search collections' }),
    t('chat.agentResources.searchActions', { defaultValue: 'Search actions' }),
  ];
  const tabIndex = TAB_VALUES.indexOf(tab);
  const searchPlaceholder = tabPlaceholders[tabIndex >= 0 ? tabIndex : 0];

  const tabLabels = useMemo(
    () => ({
      connectors: t('nav.connectors', { defaultValue: 'Connectors' }),
      collections: t('nav.collections', { defaultValue: 'Collections' }),
      actions: t('chat.agentResources.actionsTab', { defaultValue: 'Actions' }),
    }),
    [t]
  );

  const toggleGroupExpanded = (key: string) => {
    setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const handleCollectionSelectionChange = useCallback(
    (next: CollectionScopeSelection) => {
      setFilters({ ...settings.filters, apps: next.apps, kb: next.kb });
    },
    [setFilters, settings.filters]
  );

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
        />
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onToggleView}
          style={{ cursor: onToggleView ? 'pointer' : 'default', flexShrink: 0 }}
        >
          <MaterialIcon
            name={viewMode === 'overlay' ? 'close_fullscreen' : 'open_in_full'}
            size={18}
            color="var(--slate-9)"
          />
        </IconButton>
      </Flex>

      {/* Search (only on Actions tab) */}
      {tab === 'actions' && (
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
            apps={settings.filters?.apps ?? []}
            kb={settings.filters?.kb ?? []}
            onSelectionChange={handleCollectionSelectionChange}
            filterMode={tab}
          />
        )}

        {/* Actions — from my-toolsets (authenticated instances) */}
        {tab === 'actions' && (
          <>
            {toolsLoading && (
              <Flex align="center" justify="center" gap="2" style={{ padding: 'var(--space-4)' }}>
                <Spinner size="2" />
                <Text size="2" style={{ color: 'var(--gray-10)' }}>
                  {t('chat.universalAgent.loadingTools', { defaultValue: 'Loading actions…' })}
                </Text>
              </Flex>
            )}

            {toolsError && !toolsLoading && (
              <Text size="2" style={{ color: 'var(--red-9)', padding: 'var(--space-3)' }}>
                {toolsError}
              </Text>
            )}

            {!toolsLoading && !toolsError && filteredToolGroups.length === 0 && (
              <Flex direction="column" gap="2" style={{ padding: 'var(--space-3)' }}>
                <Text size="2" style={{ color: 'var(--slate-9)' }}>
                  {toolGroups.length === 0
                    ? t('chat.universalAgent.noActions', {
                        defaultValue:
                          'No authenticated actions found. Connect integrations in Workspace → Actions.',
                      })
                    : t('chat.agentResources.noActionMatches', {
                        defaultValue: 'No actions match your search.',
                      })}
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
                      {t('chat.agentResources.browseWorkspaceActions', {
                        defaultValue: 'Browse workspace actions',
                      })}
                    </Text>
                  </Flex>
                  <IconButton asChild size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
                    <Link href="/workspace/actions" aria-label="Open workspace actions">
                      <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
                    </Link>
                  </IconButton>
                </Flex>
              </Flex>
            )}

            {!toolsLoading && !toolsError && filteredToolGroups.length > 0 && (
              <>
                {filteredToolGroups.map((group) => {
                  const groupKey = `toolset:${group.instanceId}`;
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
                              disabled={atToolCap && checkState === false}
                              checked={checkState}
                              onCheckedChange={(v) => {
                                setGroupToolsEnabled(group.fullNames, v === true);
                              }}
                            />
                          </div>
                          <UniversalToolsetRowIcon
                            toolsetSlug={group.toolsetSlug}
                            label={group.label}
                            iconPath={group.iconPath}
                          />
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
                          const fn = bareFullName(internalKey);
                          const shortRaw = fn.includes('.')
                            ? fn.slice(fn.indexOf('.') + 1)
                            : fn;
                          const short = humanizeUnderscores(shortRaw);
                          const toolPrefix = fn.includes('.')
                            ? fn.slice(0, fn.indexOf('.'))
                            : fn;
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
                                  disabled={atToolCap && !isToolOn(internalKey)}
                                  checked={isToolOn(internalKey)}
                                  onCheckedChange={() => toggleTool(internalKey)}
                                  onClick={(e) => e.stopPropagation()}
                                />
                              </span>
                              <ConnectorIcon type={resolveConnectorType(toolPrefix)} size={16} />
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

                {/* Configure more actions link */}
                <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-1)' }}>
                  <Text size="1" style={{ color: 'var(--gray-11)' }}>
                    {t('chat.agentResources.configureMoreActions', {
                      defaultValue: 'Configure more actions',
                    })}
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
                        {t('chat.agentResources.browseWorkspaceActions', {
                          defaultValue: 'Browse workspace actions',
                        })}
                      </Text>
                    </Flex>
                    <IconButton asChild size="1" variant="soft" color="gray" style={{ flexShrink: 0 }}>
                      <Link href="/workspace/actions" aria-label="Open workspace actions">
                        <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
                      </Link>
                    </IconButton>
                  </Flex>
                </Flex>
              </>
            )}
          </>
        )}
      </Flex>

      {/* Footer */}
      <Flex align="center" justify="between" gap="2" style={{ flexShrink: 0 }}>
        <Button type="button" size="1" variant="outline" color="gray" onClick={resetToDefaults}>
          {t('chat.agentResources.resetDefaults', { defaultValue: 'Reset to defaults' })}
        </Button>
        {tab === 'actions' && (
          <Flex align="center" gap="2">
            {atToolCap && (
              <Text size="1" style={{ color: 'var(--amber-11)' }}>
                {t('chat.universalAgent.toolCapReached', { defaultValue: 'Tool limit reached' })}
              </Text>
            )}
            <Text size="1" style={{ color: 'var(--gray-9)' }}>
              {t('chat.universalAgent.toolCount', {
                count: selectedCount,
                max: MAX_USER_TOOLS,
                defaultValue: `{{count}} / {{max}} tools`,
              })}
            </Text>
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}
