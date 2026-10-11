import { describe, expect, it } from 'vitest';
import { mcpSignInServers } from '../mcp-sign-in';
import type { MessagePart } from '../types';

describe('mcpSignInServers', () => {
  it("reads the reply's sign-in part, dropping what isn't a server", () => {
    const parts = [
      { type: 'text', content: 'Done.', isFinal: true },
      {
        type: 'mcp_sign_in',
        servers: [
          { instanceId: 'inst-drive', serverName: 'Drive', scopes: ['files.write', 7, ''], agentKey: 'agent-7' },
          { instanceId: '', serverName: 'Nameless' },
          'junk',
          { instanceId: 'inst-gh', serverName: '' },
        ],
      },
    ] as unknown as MessagePart[];

    expect(mcpSignInServers(parts)).toEqual([
      { instanceId: 'inst-drive', serverName: 'Drive', scopes: ['files.write'], agentKey: 'agent-7' },
      { instanceId: 'inst-gh', serverName: 'inst-gh', scopes: [] },
    ]);
  });

  it('is empty without one', () => {
    expect(mcpSignInServers(undefined)).toEqual([]);
    expect(mcpSignInServers([{ type: 'text', content: 'x' }])).toEqual([]);
    expect(mcpSignInServers([{ type: 'mcp_sign_in' } as MessagePart])).toEqual([]);
  });
});
