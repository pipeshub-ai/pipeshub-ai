'use client';

import { create } from 'zustand';

/**
 * Whether an admin approves this org's MCP tools (the hub on, `mcpHub`, and its approval required,
 * `mcpToolApproval`: D-MCP-5, an edition's setting) and whether STDIO servers run in runners
 * (`MCP_STDIO_RUNNERS`: runner settings), as the org's MCP list reports them. Never reported, or absent,
 * reads as off; a server from before `mcpToolApproval` reports approvals by `mcpHub` alone.
 */
interface McpDeploymentState {
  hub: boolean;
  stdioRunners: boolean;
}

export const useMcpDeploymentStore = create<McpDeploymentState>()(() => ({ hub: false, stdioRunners: false }));

/** Records what the MCP list answered. */
export function observeMcpDeployment(body: unknown): McpDeploymentState {
  const answer = body !== null && typeof body === 'object' ? (body as Record<string, unknown>) : {};
  const next = {
    hub: answer.mcpHub === true && answer.mcpToolApproval !== false,
    stdioRunners: answer.mcpStdioRunners === true,
  };
  const now = useMcpDeploymentStore.getState();
  if (now.hub !== next.hub || now.stdioRunners !== next.stdioRunners) useMcpDeploymentStore.setState(next);
  return next;
}
