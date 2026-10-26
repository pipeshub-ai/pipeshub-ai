import { describe, expect, it } from 'vitest';
import { orgInstancesWithStatus } from '../team/store';
import type { McpMyServerEntry, McpServerInstance } from '../types';

function record(overrides: Partial<McpServerInstance> = {}): McpServerInstance {
  return {
    _id: 'org-1',
    orgId: 'o',
    createdBy: 'admin',
    name: 'GitHub',
    transport: 'streamable_http',
    authMode: 'oauth',
    useAdminAuth: false,
    args: [],
    requiredEnv: [],
    optionalEnv: [],
    scopes: ['repo'],
    url: 'https://api.example.com/mcp',
    authorizationUrl: 'https://auth.example.com/authorize',
    isCustom: true,
    createdAt: 1,
    updatedAt: 1,
    scope: 'org',
    ...overrides,
  };
}

describe('orgInstancesWithStatus', () => {
  it('edits the full record and adds the admin connection status', () => {
    // As /my-mcp-servers answers when it could not confirm the caller as an admin.
    const { url: _url, authorizationUrl: _auth, scopes: _scopes, ...withoutConnection } = record();
    const status: McpMyServerEntry = { ...withoutConnection, isAuthenticated: true, tools: [], toolsError: null };

    const [merged] = orgInstancesWithStatus([record()], [status]);

    expect(merged.url).toBe('https://api.example.com/mcp');
    expect(merged.authorizationUrl).toBe('https://auth.example.com/authorize');
    expect(merged.scopes).toEqual(['repo']);
    expect(merged.isAuthenticated).toBe(true);
  });

  it('leaves personal servers out and defaults a missing status to not connected', () => {
    const merged = orgInstancesWithStatus([record({ _id: 'p', scope: 'personal' }), record({ _id: 'new' })], []);

    expect(merged.map((i) => i._id)).toEqual(['new']);
    expect(merged[0].isAuthenticated).toBe(false);
    expect(merged[0].tools).toEqual([]);
  });
});
