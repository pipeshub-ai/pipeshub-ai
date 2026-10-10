import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { McpServersApi } from '../api';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), put: vi.fn() } }));

const get = vi.mocked(apiClient.get);
const put = vi.mocked(apiClient.put);

beforeEach(() => {
  get.mockReset().mockResolvedValue({ data: { tools: {} } });
  put.mockReset().mockResolvedValue({ data: { tools: {} } });
});

describe('McpServersApi tool approval rules', () => {
  it('reads and sets the company rules of a server', async () => {
    await McpServersApi.getToolPolicy('inst/1');
    await McpServersApi.updateToolPolicy('inst/1', { tools: { x: { rule: 'block' } } });
    expect(get).toHaveBeenCalledWith('/api/v1/mcp-servers/instances/inst%2F1/tool-policy');
    expect(put).toHaveBeenCalledWith('/api/v1/mcp-servers/instances/inst%2F1/tool-policy', { tools: { x: { rule: 'block' } } });
  });

  it("reads and sets the caller's own rules", async () => {
    await McpServersApi.getMyToolRules('inst-1');
    await McpServersApi.updateMyToolRules('inst-1', { tools: { x: 'allow' } });
    expect(get).toHaveBeenCalledWith('/api/v1/mcp-servers/instances/inst-1/my-tool-rules');
    expect(put).toHaveBeenCalledWith('/api/v1/mcp-servers/instances/inst-1/my-tool-rules', { tools: { x: 'allow' } });
  });

  it("reads and sets an agent's rules", async () => {
    await McpServersApi.getAgentToolRules('agent 1', 'inst-1');
    await McpServersApi.updateAgentToolRules('agent 1', 'inst-1', { tools: { x: 'ask' } });
    expect(get).toHaveBeenCalledWith('/api/v1/mcp-servers/agents/agent%201/instances/inst-1/tool-rules');
    expect(put).toHaveBeenCalledWith('/api/v1/mcp-servers/agents/agent%201/instances/inst-1/tool-rules', { tools: { x: 'ask' } });
  });
});
