'use client';

import { useState, useEffect } from 'react';

/** GitHub API URL for fetching repo metadata (stars, forks, etc.) */
const GITHUB_API_URL = 'https://api.github.com/repos/pipeshub-ai/pipeshub-ai';

/**
 * Fetches the GitHub star count from the GitHub API
 * and returns it formatted (e.g. "2.5k").
 * Returns null while loading or on error.
 */
export function useGitHubStars() {
  const [stars, setStars] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(GITHUB_API_URL)
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled && typeof data.stargazers_count === 'number') {
          const count = data.stargazers_count;
          setStars(
            count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count)
          );
        }
      })
      .catch(() => {
        /* silently fail — stars are decorative */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return stars;
}
