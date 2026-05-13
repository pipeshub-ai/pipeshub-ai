'use client';

import React, { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
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
import { useChatStore } from '@/chat/store';
import { CollectionRow } from './connectors-collections/collection-row';

type ExpansionViewMode = 'inline' | 'overlay';

const TAB_VALUES = ['connectors', 'collections', 'actions'] as const;
type TabValue = (typeof TAB_VALUES)[number];

/** Figma segmented control track (`7523:16159` / Settings spec): fixed width, 32px height, 4px radius */
const FIGMA_TABLIST_WIDTH = 246;
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
      aria-label="Chat filters"
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

interface AgentScopedResourcesPanelProps {
  onToggleView?: () => void;
  viewMode?: ExpansionViewMode;
}

function setsEqualAsSets(a: string[], b: string[]): boolean {
  if (a === b) return true;
  const sa = new Set(a);
  const sb = new Set(b);
  if (sa.size !== sb.size) return false;
  return Array.from(sa).every((x) => sb.has(x));
}

function effectiveKnowledge(
  scope: { apps: string[]; kb: string[] } | null,
  defaults: { apps: string[]; kb: string[] }
): { apps: string[]; kb: string[] } {
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

/**
 * Connectors · Collections · Actions panel for agent-scoped chat (`?agentId=`).
 * Matches Settings spec (Figma `7523:16157` — segmented tabs, search row, actions summary rows, configure-more links).
 */
export function AgentScopedResourcesPanel({
  onToggleView,
  viewMode = 'inline',
}: AgentScopedResourcesPanelProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabValue>('connectors');
  const [search, setSearch] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const connectors = useChatStore((s) => s.agentChatConnectors);
  const collectionRows = useChatStore((s) => s.agentKnowledgeCollectionRows);
  const toolGroups = useChatStore((s) => s.agentChatToolGroups);
  const defaults = useChatStore((s) => s.agentKnowledgeDefaults);
  const scope = useChatStore((s) => s.agentKnowledgeScope);
  const selectedTools = useChatStore((s) => s.agentStreamTools);
  const setScope = useChatStore((s) => s.setAgentKnowledgeScope);
  const setTools = useChatStore((s) => s.setAgentStreamTools);

  const eff = useMemo(() => effectiveKnowledge(scope, defaults), [scope, defaults]);

  /**
   * User turned off every configured knowledge source (defaults still list agent knowledge).
   * Omit when the agent never had knowledge (tools-only / retrieval-less agents).
   */
  const showKnowledgeClearedWarning = useMemo(
    () =>
      eff.apps.length === 0 &&
      eff.kb.length === 0 &&
      (defaults.apps.length > 0 || defaults.kb.length > 0),
    [eff.apps, eff.kb, defaults.apps, defaults.kb]
  );

  const tryNormalizeKnowledgeScope = useCallback(
    (apps: string[], kb: string[]) => {
      if (setsEqualAsSets(apps, defaults.apps) && setsEqualAsSets(kb, defaults.kb)) {
        setScope(null);
      } else {
        setScope({ apps, kb });
      }
    },
    [defaults.apps, defaults.kb, setScope]
  );

  const toggleConnector = useCallback(
    (id: string) => {
      const nextApps = eff.apps.includes(id)
        ? eff.apps.filter((x) => x !== id)
        : [...eff.apps, id];
      tryNormalizeKnowledgeScope(nextApps, eff.kb);
    },
    [eff.apps, eff.kb, tryNormalizeKnowledgeScope]
  );

  const toggleKb = useCallback(
    (id: string) => {
      const nextKb = eff.kb.includes(id)
        ? eff.kb.filter((x) => x !== id)
        : [...eff.kb, id];
      tryNormalizeKnowledgeScope(eff.apps, nextKb);
    },
    [eff.apps, eff.kb, tryNormalizeKnowledgeScope]
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
      const cat = useChatStore.getState().agentToolCatalogFullNames;
      const cur = useChatStore.getState().agentStreamTools;

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
    [setTools]
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
      const cat = useChatStore.getState().agentToolCatalogFullNames;
      const explicit = useChatStore.getState().agentStreamTools;

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
    [setTools]
  );

  const resetToAgentDefaults = useCallback(() => {
    setScope(null);
    setTools(null);
  }, [setScope, setTools]);

  const filteredConnectors = useMemo(() => {
    if (!search.trim()) return connectors;
    const q = search.toLowerCase();
    return connectors.filter((c) => c.label.toLowerCase().includes(q) || c.id.toLowerCase().includes(q));
  }, [connectors, search]);

  const filteredCollections = useMemo(() => {
    if (!search.trim()) return collectionRows;
    const q = search.toLowerCase();
    return collectionRows.filter((r) => r.name.toLowerCase().includes(q) || r.id.toLowerCase().includes(q));
  }, [collectionRows, search]);

  const filteredToolGroups = useMemo(() => {
    return toolGroups
      .map((g) => ({
        ...g,
        fullNames: g.fullNames.filter((fn) => {
          if (!search.trim()) return true;
          const q = search.toLowerCase();
          const desc = (g.toolDescriptions?.[fn] ?? '').toLowerCase();
          return (
            fn.toLowerCase().includes(q) ||
            g.label.toLowerCase().includes(q) ||
            desc.includes(q)
          );
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
            {filteredConnectors.length === 0 ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {t('chat.agentResources.noConnectors', {
                  defaultValue: 'No connectors configured for this agent.',
                })}
              </Text>
            ) : (
              filteredConnectors.map((c) => (
                <Flex
                  key={c.id}
                  align="center"
                  justify="between"
                  gap="2"
                  onClick={() => toggleConnector(c.id)}
                  style={{
                    ...OLIVE_ROW,
                    padding: 'var(--space-2) var(--space-3)',
                    cursor: 'pointer',
                  }}
                >
                  <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
                    <span style={CHECKBOX_ALIGN}>
                      <Checkbox
                        size="1"
                        checked={eff.apps.includes(c.id)}
                        onCheckedChange={() => toggleConnector(c.id)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </span>
                    <ConnectorIcon
                      type={resolveConnectorType(connectorIconHint(c))}
                      size={20}
                    />
                    <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
                      {c.label}
                    </Text>
                  </Flex>
                </Flex>
              ))
            )}
          </>
        )}

        {tab === 'collections' && (
          <>
            {collectionRows.length === 0 ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {t('chat.agentResources.noCollections', {
                  defaultValue: 'No collections configured for this agent.',
                })}
              </Text>
            ) : filteredCollections.length === 0 ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {t('chat.agentResources.noCollectionMatches', {
                  defaultValue: 'No collections match your search.',
                })}
              </Text>
            ) : (
              filteredCollections.map((row) => (
                <CollectionRow
                  key={row.id}
                  id={row.id}
                  name={row.name}
                  sourceType={row.sourceType}
                  isSelected={eff.kb.includes(row.id)}
                  onToggle={toggleKb}
                />
              ))
            )}
          </>
        )}

        {tab === 'actions' && (
          <>
            {filteredToolGroups.length === 0 ? (
              <Text size="2" style={{ color: 'var(--slate-9)', padding: 'var(--space-3)' }}>
                {t('chat.agentResources.noActions', {
                  defaultValue: 'No actions (toolsets) configured for this agent.',
                })}
              </Text>
            ) : (
              <>
                {filteredToolGroups.map((group) => {
                  const groupKey = group.instanceId
                    ? `toolset:${group.instanceId}`
                    : `${group.toolsetSlug || 'toolset'}:${group.label}:${group.fullNames[0] ?? ''}`;
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
                          <AgentToolsetRowIcon
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
                        group.fullNames.map((fullName) => {
                          const shortRaw = fullName.includes('.')
                            ? fullName.slice(fullName.indexOf('.') + 1)
                            : fullName;
                          const short = humanizeUnderscores(shortRaw);
                          const toolPrefix = fullName.includes('.')
                            ? fullName.slice(0, fullName.indexOf('.'))
                            : fullName;
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
                              <ConnectorIcon type={resolveConnectorType(toolPrefix)} size={16} />
                              <Flex direction="column" gap="0" style={{ flex: 1, minWidth: 0 }}>
                                <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }} truncate>
                                  {short}
                                </Text>
                                <Text size="1" style={{ color: 'var(--gray-9)' }} truncate>
                                  {fullName}
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
                      <Link
                        href="/workspace/actions"
                        aria-label={t('chat.agentResources.browseWorkspaceActionsAria', {
                          defaultValue: 'Open workspace actions to add or manage integrations',
                        })}
                      >
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

      <Flex align="center" justify="between" gap="2" style={{ flexShrink: 0 }}>
        <Button type="button" size="1" variant="outline" color="gray" onClick={resetToAgentDefaults}>
          {t('chat.agentResources.resetDefaults', { defaultValue: 'Reset to defaults' })}
        </Button>
      </Flex>
    </Flex>
  );
}
