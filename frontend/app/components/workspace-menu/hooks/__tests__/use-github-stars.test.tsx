import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('@/lib/branding', () => ({ useBrand: () => ({ githubUrl: 'https://github.com/acme/widgets' }) }));

const API = 'https://api.github.com/repos/acme/widgets';
const FAILED_KEY = `pipeshub:github-stars:v2:${API}:failed`;

const backing = new Map<string, string>();
const fetchMock = vi.fn();

async function loadHook() {
  vi.resetModules();
  return (await import('../use-github-stars')).useGitHubStars;
}

beforeEach(() => {
  backing.clear();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => (backing.has(k) ? (backing.get(k) as string) : null),
      setItem: (k: string, v: string) => void backing.set(k, String(v)),
      removeItem: (k: string) => void backing.delete(k),
      clear: () => backing.clear(),
      key: (i: number) => Array.from(backing.keys())[i] ?? null,
      get length() {
        return backing.size;
      },
    },
  });
});

afterEach(() => vi.unstubAllGlobals());

describe('useGitHubStars', () => {
  it('shows the count of a successful fetch', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ stargazers_count: 3700 }) });
    const useGitHubStars = await loadHook();

    const { result } = renderHook(() => useGitHubStars());

    await waitFor(() => expect(result.current).toBe('3.7k'));
  });

  it('remembers a refused fetch across a reload, so the reload does not ask GitHub again', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 403 });
    const first = await loadHook();
    const { unmount } = renderHook(() => first());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(backing.has(FAILED_KEY)).toBe(true));
    unmount();

    // A reload: fresh module state, same localStorage.
    const second = await loadHook();
    const { result } = renderHook(() => second());
    await new Promise((r) => setTimeout(r, 20));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current).toBeNull();
  });

  it('asks again once the backoff has passed', async () => {
    backing.set(FAILED_KEY, String(Date.now() - 6 * 60 * 1000));
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ stargazers_count: 12 }) });
    const useGitHubStars = await loadHook();

    const { result } = renderHook(() => useGitHubStars());

    await waitFor(() => expect(result.current).toBe('12'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
