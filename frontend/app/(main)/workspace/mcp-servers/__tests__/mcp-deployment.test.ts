/**
 * H3 FG-1: the org's MCP list tells the browser whether the hub (`MCP_HUB`)
 * and STDIO runners (`MCP_STDIO_RUNNERS`) are on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { McpServersApi } from '../api';
import { observeMcpDeployment, useMcpDeploymentStore } from '../hub/deployment';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

const get = vi.mocked(apiClient.get);

beforeEach(() => useMcpDeploymentStore.setState({ hub: false, stdioRunners: false }));
afterEach(() => vi.clearAllMocks());

describe('MCP deployment flags', () => {
  it('records both flags from the list the admin page reads', async () => {
    get.mockResolvedValueOnce({ data: { instances: [], mcpHub: true, mcpStdioRunners: true } });
    await McpServersApi.getMyMcpServers(false);
    expect(useMcpDeploymentStore.getState()).toMatchObject({ hub: true, stdioRunners: true });

    get.mockResolvedValueOnce({ data: { instances: [], mcpHub: false, mcpStdioRunners: true } });
    await McpServersApi.getMyMcpServers(false);
    expect(useMcpDeploymentStore.getState()).toMatchObject({ hub: false, stdioRunners: true });
  });

  it('reads an absent or non-boolean field as off', () => {
    expect(observeMcpDeployment({ instances: [] })).toEqual({ hub: false, stdioRunners: false });
    expect(observeMcpDeployment({ mcpHub: 'true', mcpStdioRunners: 1 })).toEqual({ hub: false, stdioRunners: false });
    expect(observeMcpDeployment(null)).toEqual({ hub: false, stdioRunners: false });
  });

  it('D-MCP-5: the approvals tab shows only where an admin approval is required', () => {
    expect(observeMcpDeployment({ mcpHub: true, mcpToolApproval: false })).toEqual({ hub: false, stdioRunners: false });
    expect(observeMcpDeployment({ mcpHub: true, mcpToolApproval: true })).toEqual({ hub: true, stdioRunners: false });
    // A server from before the field reports approvals by the hub alone.
    expect(observeMcpDeployment({ mcpHub: true })).toEqual({ hub: true, stdioRunners: false });
  });
});
