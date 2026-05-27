'use client';

import { useState, useEffect } from 'react';
import { UsersApi } from '@/app/(main)/workspace/users/api';

export interface GraphUserEntry {
  fullName: string;
  /** MongoDB ObjectID — use for avatar URL `/api/v1/users/{mongoId}/dp` */
  mongoId: string | null;
  /** Base64 data URI for the profile picture, if set by the user. Takes priority over the dp endpoint. */
  profilePicture: string | null;
}

/**
 * Resolves a graph DB user ID (the ArangoDB UUID stored in agent/team `createdBy`)
 * to a display name and optional MongoDB ID for avatar construction.
 *
 * Internally paginates GET /api/v1/users/graph/list until the matching user is found
 * (typically 1 request for orgs with ≤ 100 users).
 *
 * Returns null while loading or when the user cannot be resolved.
 */
export function useGraphUserEntry(
  graphId: string | null | undefined
): GraphUserEntry | null {
  const [entry, setEntry] = useState<GraphUserEntry | null>(null);

  useEffect(() => {
    if (!graphId) {
      setEntry(null);
      return;
    }

    let cancelled = false;
    setEntry(null);

    void (async () => {
      try {
        const result = await UsersApi.getGraphUsersByIds([graphId]);
        if (cancelled) return;
        const user = result[graphId];
        if (user) {
            setEntry({
              fullName: user.name?.trim() || '',
              mongoId: user.userId?.trim() || null,
              profilePicture: user.profilePicture?.trim() || null,
            });
        } else {
          setEntry(null);
        }
      } catch {
        if (!cancelled) setEntry(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [graphId]);

  return entry;
}
