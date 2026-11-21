import type React from 'react';

/** The org MCP server an edit panel shows, as an edition's settings for it need it. */
export interface McpInstanceAdminProps {
  instanceId: string;
  transport: string | undefined;
  insideOrganization: boolean;
}

/** A tab an edition adds to an org MCP server's edit panel, or null (OSS: none). Called on every render. */
export function useMcpInstanceAdminTab(
  _instance: McpInstanceAdminProps | null,
): { label: string; content: React.ReactNode } | null {
  return null;
}

/** Settings an edition adds under an org MCP server's configuration (OSS: none). */
export function McpInstanceConfigExtras(_props: McpInstanceAdminProps): React.ReactNode {
  return null;
}
