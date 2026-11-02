'use client';

import useSWR from 'swr';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { isLookupUserId } from '@/lib/utils/user-ids';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';

type DirectoryUserKey = readonly [typeof SWR_NS.userDirectory, string];
type Waiter = { resolve: (user: User | null) => void; reject: (error: unknown) => void };

const queued = new Map<string, Waiter[]>();

function flush(): void {
  const batch = new Map(queued);
  queued.clear();
  // A card falls back to the id or initials on its own; a toast per failed lookup would only be noise.
  UsersApi.getUsersByIds([...batch.keys()], { suppressErrorToast: true }).then(
    (users) => {
      const byId = new Map(users.map((user) => [user.userId.toLowerCase(), user]));
      batch.forEach((waiters, id) => waiters.forEach((w) => w.resolve(byId.get(id) ?? null)));
    },
    (error: unknown) => batch.forEach((waiters) => waiters.forEach((w) => w.reject(error))),
  );
}

/** Joins every id asked for in the same tick into one by-ids request. */
function fetchDirectoryUser([, id]: DirectoryUserKey): Promise<User | null> {
  return new Promise((resolve, reject) => {
    if (queued.size === 0) queueMicrotask(flush);
    queued.set(id, [...(queued.get(id) ?? []), { resolve, reject }]);
  });
}

/**
 * A user's name, email and picture from the org directory, or `null` while
 * loading, when unknown, or when the lookup failed. Looked up once per session
 * and shared by every caller; user edits in this app refresh it.
 */
export function useUserDirectoryEntry(userId: string | null | undefined): User | null {
  const id = userId?.trim().toLowerCase();
  const key: DirectoryUserKey | null = isLookupUserId(id) ? [SWR_NS.userDirectory, id] : null;
  const { data } = useSWR(key, fetchDirectoryUser, SWR_POLICY.sessionStable);
  return data ?? null;
}
