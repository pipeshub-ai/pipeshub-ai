import { describe, expect, it } from 'vitest';
import {
  NO_AUTH_SCHEME,
  authSchemeOptions,
  buildCredentials,
  buildPayload,
  connectionMode,
  credentialFields,
  formFromConfig,
  formFromSpec,
  formProblems,
  rebaseFormOnSpec,
  toggleOperation,
  usableByServiceAccount,
  withAuthScheme,
} from '../toolset-form';
import { compiledSpec, operation, toolset, toolsetConfig } from './fixtures';

const oauthCode = {
  name: 'oauth',
  type: 'oauth2_authorization_code' as const,
  description: '',
  scopes: { 'orders:read': 'Read orders', 'orders:write': 'Write orders' },
};
const clientCredentials = { name: 'cc', type: 'oauth2_client_credentials' as const, description: '', scopes: {} };

describe('formFromSpec', () => {
  it('names the toolset after the spec, uses the first server and scheme, and preselects only reads', () => {
    const form = formFromSpec(compiledSpec());
    expect(form.name).toBe('Orders API');
    expect(form.baseUrl).toBe('https://orders.internal/v1');
    expect(form.authSchemeName).toBe('apiKey');
    expect(form.operations).toEqual(['listOrders']);
    expect(form.useSharedCredential).toBe(false);
    expect(form.timeoutSeconds).toBe(30);
  });

  it('falls back to no auth and an empty base URL when the spec declares neither', () => {
    const form = formFromSpec(compiledSpec({ servers: [], authSchemes: [] }));
    expect(form.authSchemeName).toBe('none');
    expect(form.baseUrl).toBe('');
  });

  it('turns on the shared credential when the first scheme is client credentials', () => {
    expect(formFromSpec(compiledSpec({ authSchemes: [clientCredentials] })).useSharedCredential).toBe(true);
  });
});

describe('authSchemeOptions', () => {
  it('always offers no authentication first', () => {
    expect(authSchemeOptions(compiledSpec()).map((s) => s.name)).toEqual(['none', 'apiKey']);
  });
});

describe('rebaseFormOnSpec', () => {
  it('keeps settings and the chosen operations the new spec still offers', () => {
    const before = { ...formFromConfig(toolsetConfig()), name: 'Mine', operations: ['listOrders', 'gone'] };
    const after = rebaseFormOnSpec(before, compiledSpec());
    expect(after.name).toBe('Mine');
    expect(after.operations).toEqual(['listOrders']);
    expect(after.authSchemeName).toBe('apiKey');
  });

  it("moves to the new spec's default scheme when the chosen one is gone", () => {
    const before = { ...formFromConfig(toolsetConfig()), authSchemeName: 'removed', scopes: ['x'] };
    const after = rebaseFormOnSpec(before, compiledSpec());
    expect(after.authSchemeName).toBe('apiKey');
    expect(after.scopes).toEqual([]);
  });
});

describe('withAuthScheme', () => {
  const form = { ...formFromSpec(compiledSpec()), scopes: ['a'], useSharedCredential: true };

  it('clears scopes and keeps the shared choice for another secured scheme', () => {
    expect(withAuthScheme(form, oauthCode)).toMatchObject({ authSchemeName: 'oauth', scopes: [], useSharedCredential: true });
  });

  it('turns sharing off for no auth and forces it on for client credentials', () => {
    expect(withAuthScheme(form, NO_AUTH_SCHEME).useSharedCredential).toBe(false);
    expect(withAuthScheme({ ...form, useSharedCredential: false }, clientCredentials).useSharedCredential).toBe(true);
  });
});

describe('toggleOperation', () => {
  const ops = [operation('a', 'read'), operation('b', 'write'), operation('c', 'read')];

  it("adds in the spec's order and removes", () => {
    expect(toggleOperation(ops, ['c'], 'a', true)).toEqual(['a', 'c']);
    expect(toggleOperation(ops, ['a', 'c'], 'a', false)).toEqual(['c']);
  });
});

describe('formProblems', () => {
  const valid = formFromSpec(compiledSpec());

  it('accepts a complete form', () => {
    expect(formProblems(valid, 'api_key')).toEqual([]);
  });

  it('names each missing or out-of-range field', () => {
    const bad = { ...valid, name: ' ', baseUrl: '', operations: [], timeoutSeconds: 121 };
    expect(formProblems(bad, 'api_key')).toEqual(['name', 'baseUrl', 'operations', 'timeout']);
    expect(formProblems({ ...valid, timeoutSeconds: 0 }, 'api_key')).toEqual(['timeout']);
  });

  it('requires an OAuth client ID only for the sign-in flow', () => {
    expect(formProblems(valid, 'oauth2_authorization_code')).toEqual(['oauthClientId']);
    expect(formProblems(valid, 'oauth2_client_credentials')).toEqual([]);
  });
});

describe('buildPayload', () => {
  const form = {
    ...formFromSpec(compiledSpec()),
    name: '  Orders ',
    baseUrl: ' https://x ',
    scopes: ['orders:read'],
    oauthClientId: ' cid ',
    oauthClientSecret: '',
  };

  it('sends the spec only when one was uploaded', () => {
    expect(buildPayload(form, 'api_key', 'openapi: 3.1.0')).toMatchObject({ spec: 'openapi: 3.1.0', name: 'Orders', baseUrl: 'https://x' });
    expect(buildPayload(form, 'api_key')).not.toHaveProperty('spec');
  });

  it('sends OAuth app fields only for the sign-in flow, and no blank secret', () => {
    const signIn = buildPayload(form, 'oauth2_authorization_code');
    expect(signIn.oauthClientId).toBe('cid');
    expect(signIn).not.toHaveProperty('oauthClientSecret');
    expect(signIn.scopes).toEqual(['orders:read']);
    expect(buildPayload({ ...form, oauthClientSecret: 's' }, 'oauth2_authorization_code').oauthClientSecret).toBe('s');

    const key = buildPayload(form, 'api_key');
    expect(key).not.toHaveProperty('oauthClientId');
    expect(key.scopes).toEqual([]);
  });

  it('forces the shared credential for client credentials and never shares for no auth', () => {
    expect(buildPayload({ ...form, useSharedCredential: false }, 'oauth2_client_credentials').useSharedCredential).toBe(true);
    expect(buildPayload({ ...form, useSharedCredential: true }, 'none').useSharedCredential).toBe(false);
  });
});

describe('credentials by auth type', () => {
  it('asks for the fields each auth type uses', () => {
    expect(credentialFields('none')).toEqual([]);
    expect(credentialFields('api_key')).toEqual(['apiKey']);
    expect(credentialFields('http_bearer')).toEqual(['token']);
    expect(credentialFields('http_basic')).toEqual(['username', 'password']);
    expect(credentialFields('oauth2_client_credentials')).toEqual(['clientId', 'clientSecret']);
    expect(credentialFields('oauth2_authorization_code')).toEqual([]);
  });

  it('builds a body only when every field is filled, trimming all but the password', () => {
    expect(buildCredentials('http_basic', { username: ' me ', password: ' p w ' })).toEqual({ username: 'me', password: ' p w ' });
    expect(buildCredentials('http_basic', { username: 'me', password: '  ' })).toBeNull();
    expect(buildCredentials('api_key', { apiKey: 'k\n', token: 'ignored' })).toEqual({ apiKey: 'k' });
    expect(buildCredentials('none', {})).toBeNull();
  });
});

describe('who connects', () => {
  it('tells own, shared, managed and no-credential toolsets apart', () => {
    expect(connectionMode(toolset({ authType: 'none' }), true)).toBe('none');
    expect(connectionMode(toolset(), false)).toBe('own');
    expect(connectionMode(toolset({ useSharedCredential: true }), true)).toBe('shared');
    expect(connectionMode(toolset({ useSharedCredential: true }), false)).toBe('managed');
  });

  it('lets service-account agents use only shared or no-credential toolsets', () => {
    expect(usableByServiceAccount(toolset())).toBe(false);
    expect(usableByServiceAccount(toolset({ useSharedCredential: true }))).toBe(true);
    expect(usableByServiceAccount(toolset({ authType: 'none' }))).toBe(true);
  });
});
