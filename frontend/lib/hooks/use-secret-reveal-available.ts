'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '@/lib/api';

const STATUS_URL = '/api/v1/configurationManager/secretReveal';

/** Query flag a settings form adds to its GET when the admin asks to see stored secrets. */
export const REVEAL_PARAMS = { reveal: 'true' } as const;

let pending: Promise<boolean> | null = null;

/**
 * Whether this deployment lets an admin read stored secrets back. The server
 * decides again on every reveal request; this only controls whether the button
 * is drawn, so any failure means "no".
 */
export function fetchSecretRevealAvailable(): Promise<boolean> {
  if (!pending) {
    pending = apiClient
      .get<{ available?: boolean }>(STATUS_URL, { suppressErrorToast: true })
      .then(({ data }) => data?.available === true)
      .catch(() => {
        pending = null;
        return false;
      });
  }
  return pending;
}

export function useSecretRevealAvailable(enabled = true): boolean {
  const [available, setAvailable] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    void fetchSecretRevealAvailable().then((value) => {
      if (!cancelled) setAvailable(value);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return enabled && available;
}

/**
 * A reveal response belongs to the config it was requested for. Pass a value
 * (compared by identity) that changes whenever the form shows something else:
 * closed, another provider, another bot. Call the returned function as the
 * request starts and apply the response only while its result still says `true`.
 */
export function useRevealScope(scope: unknown): () => () => boolean {
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
  }, [scope]);

  return useCallback(() => {
    const requestedIn = generation.current;
    return () => requestedIn === generation.current;
  }, []);
}
