'use client';

import React from 'react';
import NextLink from 'next/link';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { approvalAction } from '../../approval-preview';
import type { MessagePart, UnavailableTool } from '../../types';

/** A tool waiting only on the person's own sign-in: one they can connect themselves. */
export const CONNECTABLE_REASON = 'not_authenticated';

const SEARCH_TOOLS = 'search_tools';

/**
 * The tools a `search_tools` step found but this agent cannot call (UX-06), read from the step's own result
 * (`{matches, unavailable: [{name, toolset, reason}]}`), which the live step and the saved conversation both keep.
 * Any other step, or a result that is not that object (cut short, an error), names none.
 */
export function unavailableFromSearch(part: Pick<MessagePart, 'toolName' | 'resultPreview'>): UnavailableTool[] {
  if (part.toolName !== SEARCH_TOOLS && !part.toolName?.endsWith(`__${SEARCH_TOOLS}`)) return [];
  let result: unknown;
  try {
    result = JSON.parse(part.resultPreview ?? '');
  } catch {
    return [];
  }
  const listed = (result as { unavailable?: unknown } | null)?.unavailable;
  if (!Array.isArray(listed)) return [];
  return listed.flatMap((entry): UnavailableTool[] => {
    if (entry === null || typeof entry !== 'object') return [];
    const { name, toolset, reason } = entry as Record<string, unknown>;
    if (typeof name !== 'string' || !name || typeof toolset !== 'string') return [];
    // A full name carries its toolset (`jira__create_issue`); the bare tool is what the row names.
    const tool = name.startsWith(`${toolset}__`) ? name.slice(toolset.length + 2) : name;
    return [{ tool, toolset, reason: typeof reason === 'string' ? reason : '' }];
  });
}

/** Each tool once, in the order found. */
export function uniqueUnavailable(tools: UnavailableTool[]): UnavailableTool[] {
  const seen = new Set<string>();
  return tools.filter((hit) => {
    const key = `${hit.toolset}\u0000${hit.tool}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Where a person connects a toolset (WS-09's pages): an MCP server on its own page, else the toolset's sign-in. */
export function connectHref(toolset: string): string {
  return toolset.startsWith('mcp')
    ? '/workspace/mcp-servers/personal/'
    : `/workspace/actions/personal/?toolsetType=${encodeURIComponent(toolset)}`;
}

/**
 * Tools the agent's search found but cannot call here (UX-06): "Jira · Create issue isn't connected" with a Connect
 * button when only the person's sign-in is missing, else that it isn't available to this agent. Plain words, no ids.
 */
export function ToolUnavailableRows({ tools }: { tools: UnavailableTool[] }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="1" data-testid="tool-unavailable">
      {tools.map((hit) => {
        const { action, app } = approvalAction(`${hit.toolset}__${hit.tool}`);
        const name = app ? `${app} · ${action}` : action;
        const connectable = hit.reason === CONNECTABLE_REASON;
        return (
          <Flex key={`${hit.toolset}:${hit.tool}`} align="center" gap="2" wrap="wrap" data-testid="tool-unavailable-row">
            <Text size="1" style={{ color: 'var(--slate-11)', flex: '1 1 12rem', minWidth: 0, overflowWrap: 'anywhere' }}>
              {connectable
                ? t('agentActivity.unavailable.notConnected', { tool: name })
                : t('agentActivity.unavailable.notAvailable', { tool: name })}
            </Text>
            {connectable ? (
              <Button asChild size="1" variant="soft" style={{ minHeight: 32 }}>
                <NextLink href={connectHref(hit.toolset)} aria-label={t('agentActivity.unavailable.connectLabel', { tool: name })}>
                  {t('agentActivity.unavailable.connect')}
                </NextLink>
              </Button>
            ) : null}
          </Flex>
        );
      })}
    </Flex>
  );
}
