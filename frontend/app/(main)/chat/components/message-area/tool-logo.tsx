'use client';

import React from 'react';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { McpServerIcon } from '@/app/(main)/workspace/mcp-servers/components';
import type { LogoStackMark } from '../answer-panel/logo-stack';
import type { UnavailableTool } from '../../unavailable-tools';

/** A tool's logo, found as the Actions and MCP servers pages find it: an MCP server we have no logo for keeps the MCP mark. */
export function ToolLogo({ item, size = 20 }: { item: UnavailableTool; size?: number }) {
  if (item.kind === 'mcp') return <McpServerIcon typeId={item.name} name={item.displayName} size={size} />;
  return <ConnectorIcon type={resolveConnectorType(item.name || item.displayName)} size={size} />;
}

/** The tools as marks for a `LogoStack`. */
export const toolMarks = (items: readonly UnavailableTool[]): LogoStackMark[] =>
  items.map((item) => ({
    key: `${item.kind}:${item.instanceId}`,
    label: item.displayName,
    logo: <ToolLogo item={item} size={12} />,
  }));
