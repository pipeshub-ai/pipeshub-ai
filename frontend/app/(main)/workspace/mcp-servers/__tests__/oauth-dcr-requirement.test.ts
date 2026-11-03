import { describe, it, expect } from 'vitest';
import {
  isOauthClientMissing,
  isOauthClientRequired,
  resolveDcrSupport,
  resolveMcpOAuthCallbackUrl,
  type DcrProbeState,
} from '../oauth-dcr-requirement';
import type { McpOAuthDiscoveryResult } from '../types';

function discoveryResult(overrides: Partial<McpOAuthDiscoveryResult> = {}): McpOAuthDiscoveryResult {
  return {
    supportsDcr: false,
    metadataFound: true,
    scopesSupported: [],
    ...overrides,
  };
}

describe('resolveDcrSupport', () => {
  it('reports true once the probe confirms a registration_endpoint (Notion/Atlassian-shaped)', () => {
    const probe: DcrProbeState = { status: 'done', result: discoveryResult({ supportsDcr: true }) };
    expect(resolveDcrSupport(probe, null)).toBe(true);
  });

  it('reports false once the probe confirms no registration_endpoint (GitHub-shaped)', () => {
    const probe: DcrProbeState = { status: 'done', result: discoveryResult({ supportsDcr: false }) };
    expect(resolveDcrSupport(probe, null)).toBe(false);
  });

  it('falls back to the template hint when the probe found no metadata at all', () => {
    const probe: DcrProbeState = {
      status: 'done',
      result: discoveryResult({ metadataFound: false, supportsDcr: false }),
    };
    expect(resolveDcrSupport(probe, true)).toBe(true);
  });

  it('is unknown (null), not "unsupported", when metadata is missing and there is no template hint', () => {
    const probe: DcrProbeState = {
      status: 'done',
      result: discoveryResult({ metadataFound: false, supportsDcr: false }),
    };
    expect(resolveDcrSupport(probe, null)).toBeNull();
    expect(resolveDcrSupport(probe, undefined)).toBeNull();
  });

  it('falls back to the template hint while the probe is loading', () => {
    expect(resolveDcrSupport({ status: 'loading' }, false)).toBe(false);
    expect(resolveDcrSupport({ status: 'loading' }, true)).toBe(true);
  });

  it('is unknown (null) while idle/loading/errored with no template hint', () => {
    expect(resolveDcrSupport({ status: 'idle' }, null)).toBeNull();
    expect(resolveDcrSupport({ status: 'loading' }, undefined)).toBeNull();
    expect(resolveDcrSupport({ status: 'error' }, null)).toBeNull();
  });

  it('never treats a probe error as confirmed "no DCR" — it defers to the template hint like loading does', () => {
    expect(resolveDcrSupport({ status: 'error' }, true)).toBe(true);
  });
});

describe('isOauthClientRequired', () => {
  it('is required only in oauth mode once DCR is confirmed unsupported', () => {
    expect(isOauthClientRequired('oauth', false)).toBe(true);
  });

  it('stays optional in oauth mode when DCR is supported', () => {
    expect(isOauthClientRequired('oauth', true)).toBe(false);
  });

  it('stays optional in oauth mode when DCR support is unknown — never blocks save on a guess', () => {
    expect(isOauthClientRequired('oauth', null)).toBe(false);
  });

  it('is never required outside oauth mode', () => {
    expect(isOauthClientRequired('api_token', false)).toBe(false);
    expect(isOauthClientRequired('none', false)).toBe(false);
  });
});

describe('isOauthClientMissing', () => {
  it('is missing when required, unconfigured, and both fields are empty', () => {
    expect(isOauthClientMissing(true, false, '', '')).toBe(true);
  });

  it('is not missing once both client id and secret are filled in', () => {
    expect(isOauthClientMissing(true, false, 'cid', 'secret')).toBe(false);
  });

  it('is not missing when an OAuth app is already configured for this instance', () => {
    expect(isOauthClientMissing(true, true, '', '')).toBe(false);
  });

  it('is never missing when credentials are not required at all', () => {
    expect(isOauthClientMissing(false, false, '', '')).toBe(false);
  });

  it('treats whitespace-only input as empty', () => {
    expect(isOauthClientMissing(true, false, '   ', '   ')).toBe(true);
  });
});

describe('resolveMcpOAuthCallbackUrl', () => {
  const serverUri = 'https://corp.example.com/pipeshub/mcp-servers/oauth/callback/';

  it("shows the server's redirect URI from the probe, sub-path included", () => {
    const probe: DcrProbeState = { status: 'done', result: discoveryResult({ redirectUri: serverUri }) };
    expect(resolveMcpOAuthCallbackUrl(probe, null, 'https://browser.example.com')).toBe(serverUri);
  });

  it("falls back to the saved OAuth app's answer while the probe hasn't one", () => {
    expect(resolveMcpOAuthCallbackUrl({ status: 'loading' }, { redirectUri: serverUri }, 'https://browser.example.com')).toBe(serverUri);
  });

  it("uses the page's own origin only until the server has answered", () => {
    expect(resolveMcpOAuthCallbackUrl({ status: 'idle' }, null, 'https://browser.example.com/')).toBe(
      'https://browser.example.com/mcp-servers/oauth/callback/'
    );
  });

  it('a server too old to answer leaves the origin in place', () => {
    const probe: DcrProbeState = { status: 'done', result: discoveryResult() };
    expect(resolveMcpOAuthCallbackUrl(probe, {}, 'https://browser.example.com')).toBe(
      'https://browser.example.com/mcp-servers/oauth/callback/'
    );
  });

  it('has nothing to show without a window or an answer', () => {
    expect(resolveMcpOAuthCallbackUrl({ status: 'error' }, null, null)).toBeNull();
  });
});
