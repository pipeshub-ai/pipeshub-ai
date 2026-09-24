import { describe, it, expect } from 'vitest';
import {
  isOauthClientMissing,
  isOauthClientRequired,
  resolveDcrSupport,
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
