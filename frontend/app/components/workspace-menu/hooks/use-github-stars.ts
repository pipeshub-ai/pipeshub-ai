'use client';

import { useEffect, useState } from 'react';

const GITHUB_API_URL = 'https://api.github.com/repos/pipeshub-ai/pipeshub-ai';
const CACHE_KEY = 'pipeshub:github-stars:v1';
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24h — star count changes slowly
const STALE_RETRY_MS = 5 * 60 * 1000; // back off 5min after a failed fetch

type CacheEntry = { value: string; fetchedAt: number };

let inMemoryCache: CacheEntry | null = null;
let inflight: Promise<string | null> | null = null;
let lastFailureAt = 0;

function formatStars(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

function readPersistedCache(): CacheEntry | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
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

function writePersistedCache(entry: CacheEntry): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(entry));
  } catch {
    /* quota / privacy mode — in-memory cache still works */
  }
}

function isFresh(entry: CacheEntry | null): entry is CacheEntry {
  return !!entry && Date.now() - entry.fetchedAt < CACHE_TTL_MS;
}

async function fetchStars(): Promise<string | null> {
  if (inflight) return inflight;
  if (Date.now() - lastFailureAt < STALE_RETRY_MS) return null;

  inflight = (async () => {
    try {
      const res = await fetch(GITHUB_API_URL);
      if (!res.ok) {
        lastFailureAt = Date.now();
        return null;
      }
      const data = (await res.json()) as { stargazers_count?: number };
      if (typeof data.stargazers_count !== 'number') {
        lastFailureAt = Date.now();
        return null;
      }
      const formatted = formatStars(data.stargazers_count);
      const entry: CacheEntry = { value: formatted, fetchedAt: Date.now() };
      inMemoryCache = entry;
      writePersistedCache(entry);
      return formatted;
    } catch {
      lastFailureAt = Date.now();
      return null;
    } finally {
      inflight = null;
    }
  })();

  return inflight;
}

/**
 * Returns the formatted GitHub star count (e.g. "2.5k").
 *
 * Cached at module level + in `localStorage` for {@link CACHE_TTL_MS} so
 * multiple consumers and remounts share a single request — keeps the
 * unauthenticated GitHub API well under its 60/hr rate limit.
 */
export function useGitHubStars(): string | null {
  const [stars, setStars] = useState<string | null>(() => {
    if (inMemoryCache) return inMemoryCache.value;
    const persisted = readPersistedCache();
    if (persisted) {
      inMemoryCache = persisted;
      return persisted.value;
    }
    return null;
  });

  useEffect(() => {
    if (isFresh(inMemoryCache)) return;

    let cancelled = false;
    fetchStars().then((value) => {
      if (!cancelled && value !== null) setStars(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return stars;
}
