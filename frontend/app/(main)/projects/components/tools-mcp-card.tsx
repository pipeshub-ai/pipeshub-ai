'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Checkbox, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { ToolsetsApi, MAX_TOOLSETS_LIST_LIMIT } from '@/app/(main)/toolsets/api';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import { useFeatureFlagsStore, selectMcpEnabled } from '@/lib/store/feature-flags-store';

interface ToolGroupRow {
  key: string;
  label: string;
  fullNames: string[];
  icon: React.ReactNode;
}

/**
 * Internal keys use `${instanceId}:${rawFullName}` — same discriminator
 * scheme `UniversalAgentResourcesPanel` uses, so a project's `tools` list is
 * wire-compatible with what the composer sends as `agentStreamTools`.
 */
function buildToolsetGroups(toolsets: BuilderSidebarToolset[]): ToolGroupRow[] {
  const groups: ToolGroupRow[] = [];
  toolsets.forEach((ts, i) => {
    const rawFullNames = (ts.tools || [])
      .map((tool) => (typeof tool.fullName === 'string' ? tool.fullName.trim() : ''))
      .filter(Boolean);
    if (rawFullNames.length === 0) return;
    const instanceId = (typeof ts.instanceId === 'string' && ts.instanceId.trim()) || `local-${i}`;
    groups.push({
      key: `toolset:${instanceId}`,
      label: (ts.instanceName || ts.displayName || ts.name || 'Tools').trim(),
      fullNames: rawFullNames.map((fn) => `${instanceId}:${fn}`),
      icon: <ConnectorIcon type={resolveConnectorType(ts.toolsetType || ts.name || '')} size={16} />,
    });
  });
  return groups;
}

function buildMcpGroups(instances: McpMyServerEntry[]): ToolGroupRow[] {
  const groups: ToolGroupRow[] = [];
  for (const entry of instances) {
    const rawFullNames = (entry.tools || [])
      .map((tool) => (typeof tool.namespacedName === 'string' ? tool.namespacedName.trim() : ''))
      .filter(Boolean);
    if (rawFullNames.length === 0) continue;
    groups.push({
      key: `mcp:${entry._id}`,
      label: (entry.name || 'MCP Server').trim(),
      fullNames: rawFullNames.map((fn) => `${entry._id}:${fn}`),
      icon: <MaterialIcon name="hub" size={16} color="var(--gray-11)" />,
    });
  }
  return groups;
}

interface ToolsMcpCardProps {
  selectedTools: string[];
  canEdit: boolean;
  onChange: (tools: string[]) => void;
}

/**
 * Project Tools & MCP picker — flat, single-page fetch of the org's
 * authenticated toolsets (`GET /api/v1/toolsets/my-toolsets`) and MCP
 * servers (`GET /api/v1/mcp/my-mcp-servers`), the same catalog
 * `UniversalAgentResourcesPanel` uses for plain (non-agent) chat. A
 * simpler flat picker than that panel's paginated/searchable tabs —
 * appropriate scope for a settings card, not a composer's live scope switcher.
 */
export function ToolsMcpCard({ selectedTools, canEdit, onChange }: ToolsMcpCardProps) {
  const { t } = useTranslation();
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const [groups, setGroups] = useState<ToolGroupRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setIsLoading(true);
      setLoadError(false);
      try {
        const [toolsetsRes, mcpRes] = await Promise.all([
          ToolsetsApi.getMyToolsets({ limit: MAX_TOOLSETS_LIST_LIMIT, authStatus: 'authenticated' }),
          mcpEnabled
            ? McpServersApi.getMyMcpServers(true)
            : Promise.resolve({ instances: [] as McpMyServerEntry[] }),
        ]);
        if (cancelled) return;
        const authenticatedMcp = (mcpRes.instances || []).filter((entry) => entry.isAuthenticated);
        setGroups([...buildToolsetGroups(toolsetsRes.toolsets), ...buildMcpGroups(authenticatedMcp)]);
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

  const selectedSet = useMemo(() => new Set(selectedTools), [selectedTools]);

  const groupCheckState = useCallback(
    (fullNames: string[]): boolean | 'indeterminate' => {
      const on = fullNames.filter((fn) => selectedSet.has(fn)).length;
      if (on === 0) return false;
      if (on === fullNames.length) return true;
      return 'indeterminate';
    },
    [selectedSet],
  );

  const toggleGroup = useCallback(
    (fullNames: string[], enabled: boolean) => {
      const next = new Set(selectedTools);
      fullNames.forEach((fn) => (enabled ? next.add(fn) : next.delete(fn)));
      onChange(Array.from(next));
    },
    [selectedTools, onChange],
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
        return (
          <Flex
            key={group.key}
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
          </Flex>
        );
      })}
    </Flex>
  );
}
