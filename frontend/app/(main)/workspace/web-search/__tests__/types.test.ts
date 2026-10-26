import { describe, expect, it } from 'vitest';
import { defaultProviderLabel } from '../types';

describe('defaultProviderLabel', () => {
  it('names the provider the server made default', () => {
    expect(defaultProviderLabel([
      { provider: 'crawl4ai', isDefault: false },
      { provider: 'searxng', isDefault: true },
    ])).toBe('SearXNG');
  });

  it('falls back to DuckDuckGo when nothing is default', () => {
    expect(defaultProviderLabel([{ provider: 'crawl4ai', isDefault: false }])).toBe('DuckDuckGo');
    expect(defaultProviderLabel([])).toBe('DuckDuckGo');
  });
});
