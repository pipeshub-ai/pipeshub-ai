import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('../axios-instance', () => ({ apiClient: { get: (...args: unknown[]) => get(...args) } }));

import { fetchFrontendPublicUrl, joinRedirectUri } from '../frontend-public-url';

function setLocation(href: string) {
  Object.defineProperty(window, 'location', { value: new URL(href), configurable: true });
}

describe('fetchFrontendPublicUrl', () => {
  const originalLocation = window.location;

  beforeEach(() => {
    get.mockReset();
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', { value: originalLocation, configurable: true });
  });

  it('returns the configured URL without a trailing slash', async () => {
    setLocation('app://./workspace');
    get.mockResolvedValue({ data: { url: 'https://pipeshub.example.com/' } });
    await expect(fetchFrontendPublicUrl()).resolves.toBe('https://pipeshub.example.com');
  });

  it('never falls back to the desktop app origin', async () => {
    setLocation('app://./workspace');
    get.mockRejectedValue(new Error('network'));
    await expect(fetchFrontendPublicUrl()).resolves.toBeNull();
  });

  it('falls back to an http(s) browser origin when the request fails', async () => {
    setLocation('https://web.example.com/workspace');
    get.mockRejectedValue(new Error('network'));
    await expect(fetchFrontendPublicUrl()).resolves.toBe('https://web.example.com');
  });

  it('shares one request between concurrent callers', async () => {
    get.mockResolvedValue({ data: { url: 'https://pipeshub.example.com' } });
    await Promise.all([fetchFrontendPublicUrl(), fetchFrontendPublicUrl()]);
    expect(get).toHaveBeenCalledTimes(1);
  });
});

describe('joinRedirectUri', () => {
  it('joins base and path with exactly one slash and keeps a trailing slash on the path', () => {
    expect(joinRedirectUri('https://a.example.com/', '/mcp-servers/oauth/callback/')).toBe(
      'https://a.example.com/mcp-servers/oauth/callback/',
    );
  });
});
