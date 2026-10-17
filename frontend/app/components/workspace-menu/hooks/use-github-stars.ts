'use client';

import { useEffect, useState } from 'react';
import { useBrand } from '@/lib/branding';
import { githubApiUrl } from '@/lib/branding/defaults';

const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24h — star count changes slowly
const STALE_RETRY_MS = 5 * 60 * 1000; // back off 5min after a failed fetch

type CacheEntry = { value: string; fetchedAt: number };

const inMemoryCache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<string | null>>();
const lastFailureAt = new Map<string, number>();

function cacheKey(apiUrl: string): string {
  return `pipeshub:github-stars:v2:${apiUrl}`;
}

function formatStars(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

function readPersistedCache(key: string): CacheEntry | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CacheEntry>;
    if (
      typeof parsed?.value === 'string' &&
      typeof parsed?.fetchedAt === 'number'
    ) {
      return { value: parsed.value, fetchedAt: parsed.fetchedAt };
    }
  } catch {
    /* corrupt entry — ignore */
  }
  return null;
}

function writePersistedCache(key: string, entry: CacheEntry): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, JSON.stringify(entry));
  } catch {
    /* quota / privacy mode — in-memory cache still works */
  }
}

function isFresh(entry: CacheEntry | null): entry is CacheEntry {
  return !!entry && Date.now() - entry.fetchedAt < CACHE_TTL_MS;
}

async function fetchStars(apiUrl: string): Promise<string | null> {
  const existing = inflight.get(apiUrl);
  if (existing) return existing;
  const failedAt = lastFailureAt.get(apiUrl) ?? 0;
  if (Date.now() - failedAt < STALE_RETRY_MS) return null;

  const request = (async () => {
    try {
      const res = await fetch(apiUrl);
      if (!res.ok) {
        lastFailureAt.set(apiUrl, Date.now());
        return null;
      }
      const data = (await res.json()) as { stargazers_count?: number };
      if (typeof data.stargazers_count !== 'number') {
        lastFailureAt.set(apiUrl, Date.now());
        return null;
      }
      const formatted = formatStars(data.stargazers_count);
      const entry: CacheEntry = { value: formatted, fetchedAt: Date.now() };
      inMemoryCache.set(apiUrl, entry);
      writePersistedCache(cacheKey(apiUrl), entry);
      return formatted;
    } catch {
      lastFailureAt.set(apiUrl, Date.now());
      return null;
    } finally {
      inflight.delete(apiUrl);
    }
  })();

  inflight.set(apiUrl, request);
  return request;
}

/**
 * Returns the formatted GitHub star count (e.g. "2.5k") for the branded repo.
 */
export function useGitHubStars(): string | null {
  const { githubUrl } = useBrand();
  const apiUrl = githubApiUrl(githubUrl);
  const [stars, setStars] = useState<string | null>(() => {
    if (!apiUrl) return null;
    const memory = inMemoryCache.get(apiUrl);
    if (memory) return memory.value;
    const persisted = readPersistedCache(cacheKey(apiUrl));
    if (persisted) {
      inMemoryCache.set(apiUrl, persisted);
      return persisted.value;
    }
    return null;
  });

  useEffect(() => {
    if (!apiUrl) {
      setStars(null);
      return;
    }
    const cached = inMemoryCache.get(apiUrl) ?? readPersistedCache(cacheKey(apiUrl));
    if (cached) {
      inMemoryCache.set(apiUrl, cached);
      setStars(cached.value);
    }
    if (isFresh(cached ?? null)) return;

    let cancelled = false;
    fetchStars(apiUrl).then((value) => {
      if (!cancelled && value !== null) setStars(value);
    });
    return () => {
      cancelled = true;
    };
  }, [apiUrl]);

  return apiUrl ? stars : null;
}
