'use client';

import React, { useMemo } from 'react';
import { Flex, IconButton, Popover, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { McpMark } from '@/app/components/ui/mcp-mark';
import { McpServerIcon } from '@/app/(main)/workspace/mcp-servers/components';
import { UniversalToolsetRowIcon } from '@/chat/components/chat-panel/expansion-panels/universal-toolset-row-icon';
import { useChatStore, type ScopedToolGroupRow } from '@/chat/store';
import { bareToolFullName } from '@/chat/tool-groups';

/** The tools picked from one action or MCP server instance, as one composer chip shows them. */
export interface SelectedToolGroup {
  instanceId: string;
  label: string;
  isMcp: boolean;
  toolsetSlug: string;
  iconPath?: string;
  typeId?: string;
  tools: { key: string; label: string }[];
}

const instanceOf = (key: string) => key.slice(0, Math.max(0, key.indexOf(':')));

/**
 * The picked tool keys (`instanceId:fullName`) grouped per instance, labelled from the known groups.
 * Keys no group knows (its list not loaded yet) still get a chip, under their instance id.
 */
export function groupSelectedTools(
  keys: readonly string[],
  groups: readonly ScopedToolGroupRow[]
): SelectedToolGroup[] {
  const picked = new Set(keys);
  const seen = new Set<string>();
  const out: SelectedToolGroup[] = [];
  for (const g of groups) {
    const tools = g.fullNames.filter((k) => picked.has(k) && !seen.has(k));
    if (tools.length === 0) continue;
    tools.forEach((k) => seen.add(k));
    out.push({
      instanceId: g.instanceId ?? instanceOf(tools[0]!),
      label: g.label,
      isMcp: g.toolsetSlug === 'mcp',
      toolsetSlug: g.toolsetSlug,
      iconPath: g.iconPath,
      typeId: g.typeId,
      tools: tools.map((k) => ({
        key: k,
        label: g.toolLabels?.[k] ?? bareToolFullName(k),
      })),
    });
  }
  const unknown = new Map<string, string[]>();
  for (const k of keys) {
    if (seen.has(k)) continue;
    const id = instanceOf(k);
    unknown.set(id, [...(unknown.get(id) ?? []), k]);
  }
  for (const [instanceId, tools] of unknown) {
    out.push({
      instanceId,
      label: '',
      isMcp: false,
      toolsetSlug: '',
      tools: tools.map((k) => ({ key: k, label: bareToolFullName(k) })),
    });
  }
  return out;
}

/** The universal agent's hand-picked tools as chips (none while every tool is on, or where tools do not apply). */
export function useSelectedToolGroups(enabled: boolean): SelectedToolGroup[] {
  const tools = useChatStore((s) => s.universalAgentStreamTools);
  const toolGroups = useChatStore((s) => s.universalAgentToolGroups);
  const mcpGroups = useChatStore((s) => s.universalAgentMcpGroups);
  return useMemo(
    () => (enabled && tools ? groupSelectedTools(tools, [...toolGroups, ...mcpGroups]) : []),
    [enabled, tools, toolGroups, mcpGroups]
  );
}

/** Takes one instance's tools off the universal agent's pick; nothing left means every tool again. */
export function removeSelectedToolGroup(instanceId: string): void {
  const store = useChatStore.getState();
  const left = (store.universalAgentStreamTools ?? []).filter((k) => instanceOf(k) !== instanceId);
  store.setUniversalAgentStreamTools(left.length > 0 ? left : null);
}

/**
 * An action shows the Actions bolt and an MCP server the MCP mark, each with its provider in the corner, the way a
 * file pill carries its app, so a chip reads "an action, from Jira" and never looks like a connector.
 */
export function ToolGroupIcon({
  group,
  size,
}: {
  group: Pick<SelectedToolGroup, 'instanceId' | 'isMcp' | 'label' | 'typeId' | 'toolsetSlug' | 'iconPath'>;
  size: number;
}) {
  const badge = Math.max(9, Math.round(size * 0.6));
  return (
    <span
      style={{
        position: 'relative',
        display: 'inline-flex',
        width: size,
        height: size,
        flexShrink: 0,
      }}
    >
      {group.isMcp ? (
        <McpMark size={size} color="var(--slate-12)" />
      ) : (
        <MaterialIcon name="bolt" size={size} color="var(--slate-12)" />
      )}
      <span
        aria-hidden
        style={{
          position: 'absolute',
          right: -3,
          bottom: -3,
          width: badge,
          height: badge,
          borderRadius: 999,
          background: 'var(--olive-1)',
          boxShadow: '0 0 0 1px var(--olive-3)',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden',
        }}
      >
        {group.isMcp ? (
          <McpServerIcon instanceId={group.instanceId} typeId={group.typeId} name={group.label} size={badge - 2} />
        ) : (
          <UniversalToolsetRowIcon
            toolsetSlug={group.toolsetSlug}
            label={group.label}
            iconPath={group.iconPath}
            size={badge - 2}
          />
        )}
      </span>
    </span>
  );
}

/** One chip per action or MCP server picked: its name and tool count, the tools in a popover, and × to drop them. */
export function SelectedToolChips({
  groups,
  onRemove,
  expandable = true,
  wrap = false,
}: {
  groups: SelectedToolGroup[];
  onRemove?: (instanceId: string) => void;
  /** Offer the list of tools behind each chip (off where the tools are listed beside it anyway). */
  expandable?: boolean;
  /** Wrap onto more lines (inside a wrapping row) instead of one line. */
  wrap?: boolean;
}) {
  const { t } = useTranslation();
  if (groups.length === 0) return null;
  return (
    // Wrapping: the chips join the parent's wrapping row instead of keeping their own line.
    <Flex align="center" gap="2" style={wrap ? { display: 'contents' } : { flexShrink: 0, minWidth: 0 }}>
      {groups.map((group) => {
        const label = group.label || t('chat.selectedTools.unknown');
        return (
          <Flex
            key={group.instanceId}
            align="center"
            gap="1"
            style={{
              height: 28,
              padding: '0 4px 0 6px',
              boxSizing: 'border-box',
              borderRadius: 'var(--radius-2)',
              border: '1px solid var(--olive-3)',
              backgroundColor: 'var(--olive-a2)',
              flexShrink: 0,
              maxWidth: 220,
            }}
          >
            <ToolGroupIcon group={group} size={16} />
            <Text size="2" truncate style={{ color: 'var(--slate-11)', minWidth: 0 }}>
              {t('chat.selectedTools.chip', {
                name: label,
                count: group.tools.length,
              })}
            </Text>
            {expandable && (
              <Popover.Root>
                <Popover.Trigger>
                  <IconButton
                    variant="ghost"
                    size="1"
                    color="gray"
                    aria-label={t('chat.selectedTools.show', { name: label })}
                    style={{ margin: 0, flexShrink: 0, cursor: 'pointer' }}
                  >
                    <MaterialIcon name="expand_more" size={16} color="var(--slate-11)" />
                  </IconButton>
                </Popover.Trigger>
                <Popover.Content size="1" style={{ maxWidth: 320, maxHeight: 280, overflowY: 'auto' }}>
                  <Flex direction="column" gap="1">
                    {group.tools.map((tool) => (
                      <Text key={tool.key} size="1" style={{ color: 'var(--slate-12)' }}>
                        {tool.label}
                      </Text>
                    ))}
                  </Flex>
                </Popover.Content>
              </Popover.Root>
            )}
            {onRemove && (
              <IconButton
                variant="ghost"
                size="1"
                color="gray"
                aria-label={t('chat.selectedTools.remove', { name: label })}
                onClick={() => onRemove(group.instanceId)}
                style={{ margin: 0, flexShrink: 0, cursor: 'pointer' }}
              >
                <MaterialIcon name="close" size={14} color="var(--slate-11)" />
              </IconButton>
            )}
          </Flex>
        );
      })}
    </Flex>
  );
}
