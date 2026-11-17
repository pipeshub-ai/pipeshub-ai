import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';

const api = vi.hoisted(() => ({ getConfig: vi.fn() }));
vi.mock('@/app/(main)/workspace/web-search/api', () => ({ WebSearchApi: api }));

import { useWebSearchConfigured } from '../use-web-search-configured';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

beforeEach(() => api.getConfig.mockReset());

describe('useWebSearchConfigured', () => {
  it('is unknown until the setting is read', async () => {
    let answer!: (value: unknown) => void;
    api.getConfig.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    const { result } = renderHook(() => useWebSearchConfigured(), { wrapper });
    expect(result.current).toBeNull();
    answer({ providers: [], configured: true });
    await waitFor(() => expect(result.current).toBe(true));
  });

  it.each([true, false])('reports what the server says (%s)', async (configured) => {
    api.getConfig.mockResolvedValue({ providers: [], configured });
    const { result } = renderHook(() => useWebSearchConfigured(), { wrapper });
    await waitFor(() => expect(result.current).toBe(configured));
  });

  it('stays unknown when the server sends no flag, so an older server keeps web search', async () => {
    api.getConfig.mockResolvedValue({ providers: [] });
    const { result } = renderHook(() => useWebSearchConfigured(), { wrapper });
    await waitFor(() => expect(api.getConfig).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });
});
