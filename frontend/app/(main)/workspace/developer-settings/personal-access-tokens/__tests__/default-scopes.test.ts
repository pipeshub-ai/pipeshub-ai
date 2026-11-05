import { describe, expect, it } from 'vitest';
import { defaultPatScopes } from '../default-scopes';

describe('defaultPatScopes', () => {
  it('ticks only scopes that read, search included', () => {
    const offered = [
      'openid',
      'offline_access',
      'semantic:write',
      'conversation:chat',
      'kb:read',
      'kb:write',
      'agent:read',
      'agent:execute',
      'connector:sync',
      'code:read',
      'project:delete',
    ];
    expect([...defaultPatScopes(offered)]).toEqual([
      'openid',
      'semantic:write',
      'kb:read',
      'agent:read',
      'code:read',
    ]);
  });
});
