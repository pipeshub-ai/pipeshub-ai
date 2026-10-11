import { describe, it, expect } from 'vitest';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import { mcpLiveView, toolsMissingFromServer } from '../mcp-live-servers';
import type { McpFlowTool } from '../sidebar-mcp-utils';

function entry(overrides: Partial<McpMyServerEntry> = {}): McpMyServerEntry {
  return {
    _id: 'inst-1',
    orgId: 'org-1',
    createdBy: 'u-1',
    name: 'github',
    typeId: 'github',
    transport: 'streamable_http',
    authMode: 'none',
    useAdminAuth: false,
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: [],
    isCustom: false,
    createdAt: 1,
    updatedAt: 1,
    isAuthenticated: true,
    tools: [{ name: 'list_issues', namespacedName: 'mcp_github_list_issues', inputSchema: {} }],
    ...overrides,
  };
}

const tool = (name: string): McpFlowTool => ({ name, fullName: `mcp_github_${name}`, description: '' });

describe('mcpLiveView', () => {
  it('knows nothing until there is a list', () => {
    expect(mcpLiveView(null, 'inst-1')).toEqual({ kind: 'unknown' });
  });

  it('a server missing from the list is unavailable', () => {
    expect(mcpLiveView([entry({ _id: 'other' })], 'inst-1')).toEqual({ kind: 'unavailable' });
  });

  it("a ready server brings the tools it offers now", () => {
    expect(mcpLiveView([entry()], 'inst-1')).toEqual({
      kind: 'live',
      state: 'ready',
      tools: [{ name: 'list_issues', fullName: 'mcp_github_list_issues', description: '' }],
    });
  });

  it.each([
    ['needs_connect', { authMode: 'oauth', isAuthenticated: false }],
    ['needs_reconnect', { toolsErrorCode: 'auth_expired', toolsError: 'expired', tools: [] }],
    ['slow', { toolsErrorCode: 'timeout', toolsTimedOut: true, tools: [] }],
    ['unreachable', { toolsErrorCode: 'unreachable', toolsError: 'down', tools: [] }],
  ] as const)('a server that is %s has no tool list to go by', (state, overrides) => {
    expect(mcpLiveView([entry(overrides as Partial<McpMyServerEntry>)], 'inst-1')).toEqual({ kind: 'live', state, tools: null });
  });
});

describe('toolsMissingFromServer', () => {
  it('names saved tools the server no longer offers', () => {
    expect(toolsMissingFromServer([tool('a'), tool('b')], [tool('a'), tool('c')])).toEqual(new Set(['b']));
  });

  it('flags nothing when the server could not be listed', () => {
    expect(toolsMissingFromServer([tool('a')], null)).toEqual(new Set());
  });
});
