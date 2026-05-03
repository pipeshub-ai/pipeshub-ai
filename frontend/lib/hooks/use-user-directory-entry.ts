'use client';

import { useState, useEffect } from 'react';
import { apiClient } from '@/lib/api';

export interface UserDirectoryEntry {
  fullName: string;
  /** Id for `/api/v1/users/{id}/dp` — from API or fallback to the requested id */
  resolvedUserId: string;
}

/** Normalizes POST `/api/v1/users/by-ids` response (array or `{ users }`). */
export function parseUserDirectoryByIdsResponse(
  data: unknown,
  fallbackUserId: string
): UserDirectoryEntry | null {
  const users = Array.isArray(data)
    ? data
    : (data as { users?: unknown[] }).users ?? [];
  if (users.length === 0) return null;
  const user = users[0] as Record<string, unknown>;
  const fullName = (user.name as string) ?? (user.fullName as string) ?? '';
  const resolvedUserId =
    (user.id as string) ?? (user._id as string) ?? fallbackUserId;
  return { fullName, resolvedUserId };
}

/** Loads a single user from the directory by id (same endpoint/shape as other callers). */
export function useUserDirectoryEntry(
  userId: string | null | undefined
): UserDirectoryEntry | null {
  const [entry, setEntry] = useState<UserDirectoryEntry | null>(null);

  useEffect(() => {
    if (!userId) {
      setEntry(null);
      return;
    }

    let cancelled = false;
    setEntry(null);

    void (async () => {
      try {
        const { data } = await apiClient.post('/api/v1/users/by-ids', {
          userIds: [userId],
        });
        if (cancelled) return;
        setEntry(parseUserDirectoryByIdsResponse(data, userId));
      } catch {
        if (!cancelled) setEntry(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [userId]);

  return entry;
}
