'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Checkbox, Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { fetchToolCatalog } from '@/chat/hooks/use-project-scope-hydration';
import { bareToolFullName, type CatalogToolGroupRow } from '@/chat/tool-groups';
import { useFeatureFlagsStore, selectMcpEnabled } from '@/lib/store/feature-flags-store';

interface ToolGroupRow {
  key: string;
  label: string;
  /** Bare `fullName`s — the wire format `applyProjectScope` intersects against. */
  fullNames: string[];
  /** Display name per bare `fullName`. */
  toolLabels: Record<string, string>;
  icon: React.ReactNode;
}

function toolLabel(bareFullName: string, fromCatalog: string | undefined): string {
  const raw = fromCatalog ?? (bareFullName.includes('.') ? bareFullName.slice(bareFullName.indexOf('.') + 1) : bareFullName);
  return raw.replace(/_/g, ' ');
}

function toCardRows(groups: CatalogToolGroupRow[], kind: 'toolset' | 'mcp'): ToolGroupRow[] {
  return groups.map((g) => ({
    key: `${kind}:${g.instanceId}`,
    label: g.label,
    fullNames: Array.from(new Set(g.fullNames.map(bareToolFullName))),
    toolLabels: Object.fromEntries(
      g.fullNames.map((key) => [bareToolFullName(key), toolLabel(bareToolFullName(key), g.toolLabels?.[key])])
    ),
    icon:
      kind === 'mcp' ? (
        <MaterialIcon name="hub" size={16} color="var(--gray-11)" />
      ) : (
        <ConnectorIcon type={resolveConnectorType(g.toolsetSlug || g.label)} size={16} />
      ),
  }));
}

interface ToolsMcpCardProps {
  selectedTools: string[];
  canEdit: boolean;
  onChange: (tools: string[]) => void;
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
      const next = new Set(selectedSet);
      if (next.has(fullName)) next.delete(fullName);
      else next.add(fullName);
      onChange(Array.from(next));
    },
    [selectedSet, onChange],
  );

  const toggleGroup = useCallback(
    (fullNames: string[], enabled: boolean) => {
      const next = new Set(selectedSet);
      fullNames.forEach((fn) => (enabled ? next.add(fn) : next.delete(fn)));
      onChange(Array.from(next));
    },
    [selectedSet, onChange],
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
              group.fullNames.map((fullName) => (
                <Flex
                  key={fullName}
                  align="center"
                  gap="2"
                  style={{
                    marginLeft: 'var(--space-5)',
                    padding: 'var(--space-1) var(--space-2)',
                    borderRadius: 'var(--radius-2)',
                    cursor: canEdit ? 'pointer' : 'default',
                  }}
                  onClick={() => canEdit && toggleTool(fullName)}
                >
                  <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
                    <Checkbox
                      size="1"
                      checked={selectedSet.has(fullName)}
                      disabled={!canEdit}
                      onCheckedChange={() => toggleTool(fullName)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </span>
                  <Text size="2" style={{ color: 'var(--slate-11)', minWidth: 0 }} truncate>
                    {group.toolLabels[fullName]}
                  </Text>
                </Flex>
              ))}
          </Flex>
        );
      })}
    </Flex>
  );
}
