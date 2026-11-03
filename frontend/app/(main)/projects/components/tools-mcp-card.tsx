'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Badge, Checkbox, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { McpServerIcon } from '@/app/(main)/workspace/mcp-servers/components';
import { fetchToolCatalog } from '@/chat/hooks/use-project-scope-hydration';
import { bareToolFullName, type CatalogToolGroupRow } from '@/chat/tool-groups';
import { toolDisplayName } from '@/app/(main)/workspace/mcp-servers/tool-rules';
import { useFeatureFlagsStore, selectMcpEnabled } from '@/lib/store/feature-flags-store';

type ToolKind = 'read' | 'write' | 'destructive';

interface ToolGroupRow {
  key: string;
  label: string;
  /** Bare `fullName`s — the wire format `applyProjectScope` intersects against. */
  fullNames: string[];
  /** Display name per bare `fullName`. */
  toolLabels: Record<string, string>;
  toolDescriptions: Record<string, string>;
  toolKinds: Record<string, ToolKind>;
  icon: React.ReactNode;
}

/** "slack.send_message" → "Send message"; an MCP tool's own name when the catalog has one. */
export function toolLabel(bareFullName: string, fromCatalog: string | undefined): string {
  const raw = fromCatalog ?? (bareFullName.includes('.') ? bareFullName.slice(bareFullName.indexOf('.') + 1) : bareFullName);
  return toolDisplayName(raw);
}

function byBareName<T>(record: Record<string, T> | undefined): Record<string, T> {
  return Object.fromEntries(Object.entries(record ?? {}).map(([key, value]) => [bareToolFullName(key), value]));
}

function toCardRows(groups: CatalogToolGroupRow[], kind: 'toolset' | 'mcp'): ToolGroupRow[] {
  return groups.map((g) => ({
    key: `${kind}:${g.instanceId}`,
    label: g.label,
    fullNames: Array.from(new Set(g.fullNames.map(bareToolFullName))),
    toolLabels: Object.fromEntries(
      g.fullNames.map((key) => [bareToolFullName(key), toolLabel(bareToolFullName(key), g.toolLabels?.[key])])
    ),
    toolDescriptions: byBareName(g.toolDescriptions),
    toolKinds: byBareName(g.toolKinds),
    icon:
      kind === 'mcp' ? (
        <McpServerIcon typeId={g.typeId} name={g.label} />
      ) : (
        <ConnectorIcon type={resolveConnectorType(g.toolsetSlug || g.label)} size={16} />
      ),
  }));
}

const KIND_ORDER: Array<ToolKind | 'unknown'> = ['read', 'write', 'destructive', 'unknown'];

/** A group's tools split by what they do to data — only when some of them are known. */
function toolSections(group: ToolGroupRow): Array<{ kind: ToolKind | 'unknown' | null; fullNames: string[] }> {
  const known = group.fullNames.some((fn) => group.toolKinds[fn] && group.toolKinds[fn] !== 'destructive');
  if (!known) return [{ kind: null, fullNames: group.fullNames }];
  return KIND_ORDER.map((kind) => ({
    kind,
    fullNames: group.fullNames.filter((fn) => (group.toolKinds[fn] ?? 'unknown') === kind),
  })).filter((section) => section.fullNames.length > 0);
}

interface ToolsMcpCardProps {
  selectedTools: string[];
  canEdit: boolean;
  /**
   * Receives an updater, not a list: clicks can outrun re-renders, so each
   * toggle applies to the parent's latest list rather than this render's.
   */
  onChange: (update: (previous: string[]) => string[]) => void;
}

/**
 * Project Tools & MCP picker over the same catalog the composer uses
 * (`fetchToolCatalog`). Persists bare `fullName`s: the server compares
 * `project.tools` against the bare names on the wire, so a stored
 * `instanceId:` prefix would never match.
 */
export function ToolsMcpCard({ selectedTools, canEdit, onChange }: ToolsMcpCardProps) {
  const { t } = useTranslation();
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const [groups, setGroups] = useState<ToolGroupRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setIsLoading(true);
      setLoadError(false);
      try {
        const catalog = await fetchToolCatalog(mcpEnabled);
        if (cancelled) return;
        setGroups([...toCardRows(catalog.toolGroups, 'toolset'), ...toCardRows(catalog.mcpGroups, 'mcp')]);
      } catch {
        if (!cancelled) setLoadError(true);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [mcpEnabled]);

  // Tolerate legacy `instanceId:`-prefixed entries already persisted on a project.
  const selectedSet = useMemo(() => new Set(selectedTools.map(bareToolFullName)), [selectedTools]);

  const groupCheckState = useCallback(
    (fullNames: string[]): boolean | 'indeterminate' => {
      const on = fullNames.filter((fn) => selectedSet.has(fn)).length;
      if (on === 0) return false;
      if (on === fullNames.length) return true;
      return 'indeterminate';
    },
    [selectedSet],
  );

  const toggleTool = useCallback(
    (fullName: string) => {
      onChange((previous) => {
        const next = new Set(previous.map(bareToolFullName));
        if (next.has(fullName)) next.delete(fullName);
        else next.add(fullName);
        return Array.from(next);
      });
    },
    [onChange],
  );

  const toggleGroup = useCallback(
    (fullNames: string[], enabled: boolean) => {
      onChange((previous) => {
        const next = new Set(previous.map(bareToolFullName));
        fullNames.forEach((fn) => (enabled ? next.add(fn) : next.delete(fn)));
        return Array.from(next);
      });
    },
    [onChange],
  );

  if (isLoading) {
    return (
      <Text size="2" style={{ color: 'var(--slate-10)' }}>
        {t('common.loading', { defaultValue: 'Loading…' })}
      </Text>
    );
  }
  if (loadError) {
    return (
      <Text size="2" style={{ color: '#ef4444' }}>
        {t('chat.projects.workspace.failedToLoad')}
      </Text>
    );
  }
  if (groups.length === 0) {
    return (
      <Text size="2" style={{ color: 'var(--slate-10)' }}>
        {t('chat.projects.workspace.noToolsAvailable', {
          defaultValue: 'No authenticated actions or MCP servers to add yet.',
        })}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="1">
      <Text size="1" style={{ color: 'var(--slate-10)', marginBottom: 'var(--space-1)' }}>
        {t('chat.projects.workspace.toolsSubtitle')}
      </Text>
      {groups.map((group) => {
        const checkState = groupCheckState(group.fullNames);
        const isExpanded = expanded[group.key] ?? false;
        return (
          <Flex key={group.key} direction="column" gap="1">
            <Flex
              align="center"
              gap="2"
              style={{
                padding: 'var(--space-2)',
                borderRadius: 'var(--radius-2)',
                background: 'var(--olive-1)',
                cursor: canEdit ? 'pointer' : 'default',
              }}
              onClick={() => canEdit && toggleGroup(group.fullNames, checkState !== true)}
            >
              <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
                <Checkbox
                  size="1"
                  checked={checkState}
                  disabled={!canEdit}
                  onCheckedChange={(v) => toggleGroup(group.fullNames, v === true)}
                  onClick={(e) => e.stopPropagation()}
                />
              </span>
              {group.icon}
              <Text
                size="2"
                style={{
                  color: 'var(--slate-12)',
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                truncate
              >
                {group.label}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-10)', flexShrink: 0 }}>
                {group.fullNames.filter((fn) => selectedSet.has(fn)).length}/{group.fullNames.length}
              </Text>
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                aria-label={isExpanded ? t('common.collapse') : t('common.expand')}
                aria-expanded={isExpanded}
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded((prev) => ({ ...prev, [group.key]: !isExpanded }));
                }}
              >
                <MaterialIcon name={isExpanded ? 'expand_less' : 'expand_more'} size={16} color="var(--gray-11)" />
              </IconButton>
            </Flex>
            {isExpanded &&
              toolSections(group).map((section) => (
                <Flex key={section.kind ?? 'all'} direction="column" gap="1">
                  {section.kind && (
                    <Text
                      size="1"
                      weight="medium"
                      style={{ color: 'var(--slate-10)', marginLeft: 'var(--space-5)', paddingTop: 'var(--space-1)' }}
                    >
                      {t(`chat.projects.workspace.toolKind.${section.kind}`)}
                    </Text>
                  )}
                  {section.fullNames.map((fullName) => {
                    const description = group.toolDescriptions[fullName];
                    const needsApproval = group.toolKinds[fullName] === 'destructive';
                    return (
                      <Flex
                        key={fullName}
                        align="start"
                        gap="2"
                        style={{
                          marginLeft: 'var(--space-5)',
                          padding: 'var(--space-1) var(--space-2)',
                          borderRadius: 'var(--radius-2)',
                          cursor: canEdit ? 'pointer' : 'default',
                        }}
                        onClick={() => canEdit && toggleTool(fullName)}
                      >
                        <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0, paddingTop: 2 }}>
                          <Checkbox
                            size="1"
                            checked={selectedSet.has(fullName)}
                            disabled={!canEdit}
                            aria-label={group.toolLabels[fullName]}
                            onCheckedChange={() => toggleTool(fullName)}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </span>
                        <Flex direction="column" style={{ minWidth: 0, flex: 1 }}>
                          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                            <Text size="2" title={fullName} style={{ color: 'var(--slate-11)', minWidth: 0 }} truncate>
                              {group.toolLabels[fullName]}
                            </Text>
                            {needsApproval && (
                              <Tooltip content={t('chat.projects.workspace.toolNeedsApprovalHint')}>
                                <Badge size="1" color="amber" variant="soft" style={{ flexShrink: 0 }}>
                                  {t('chat.projects.workspace.toolNeedsApproval')}
                                </Badge>
                              </Tooltip>
                            )}
                          </Flex>
                          {description && (
                            <Text
                              size="1"
                              style={{
                                color: 'var(--slate-10)',
                                overflow: 'hidden',
                                display: '-webkit-box',
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: 'vertical',
                              }}
                            >
                              {description}
                            </Text>
                          )}
                        </Flex>
                      </Flex>
                    );
                  })}
                </Flex>
              ))}
          </Flex>
        );
      })}
    </Flex>
  );
}
