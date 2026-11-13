import { describe, expect, it } from 'vitest';
import { defaultProviderLabel } from '../types';

describe('defaultProviderLabel', () => {
  it('names the provider the server made default', () => {
    expect(defaultProviderLabel([
      { provider: 'crawl4ai', isDefault: false },
      { provider: 'searxng', isDefault: true },
    ])).toBe('SearXNG');
  });

  it('is null when nothing is default, because web search is then off', () => {
    expect(defaultProviderLabel([{ provider: 'crawl4ai', isDefault: false }])).toBeNull();
    expect(defaultProviderLabel([])).toBeNull();
  });

  it('names DuckDuckGo only when an admin chose it', () => {
    expect(defaultProviderLabel([{ provider: 'duckduckgo', isDefault: true }])).toBe('DuckDuckGo');
  });
});
