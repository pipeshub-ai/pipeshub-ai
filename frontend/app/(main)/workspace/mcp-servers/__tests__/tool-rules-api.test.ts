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
  it("reads and sets the caller's own rules", async () => {
    await McpServersApi.getMyToolRules('inst-1');
    await McpServersApi.updateMyToolRules('inst-1', { tools: { x: 'allow' } });
    expect(get).toHaveBeenCalledWith('/api/v1/mcp-servers/instances/inst-1/my-tool-rules');
    expect(put).toHaveBeenCalledWith('/api/v1/mcp-servers/instances/inst-1/my-tool-rules', { tools: { x: 'allow' } });
  });

  it('has no company or agent rules in this edition', () => {
    expect('getToolPolicy' in McpServersApi).toBe(false);
    expect('getAgentToolRules' in McpServersApi).toBe(false);
  });
});
