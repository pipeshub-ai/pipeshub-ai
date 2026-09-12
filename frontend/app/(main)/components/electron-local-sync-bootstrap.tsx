'use client';

/**
 * ElectronLocalSyncBootstrap
 *
 * Mounts invisibly inside the (main) layout and does two things once the user
 * is signed in:
 *
 *  1. Hands the refresh token to the main process, which persists it with
 *     `safeStorage` and mints its own access tokens. This is what lets the
 *     desktop answer the server's pull with the window closed — without it,
 *     moving sync cadence server-side would buy nothing.
 *  2. Brings up the watcher for every active Local FS connector, so the
 *     journal is warm and an incremental pull is cheap.
 *
 * Enumerates from the backend so instances never opened on this machine are
 * covered; falls back to the Electron journal when the API is unreachable.
 *
 * Renders nothing — purely a side-effect component.
 */

import { useEffect, useRef } from 'react';
import { isElectron } from '@/lib/electron';
import { useAuthStore } from '@/lib/store/auth-store';
import {
  bootstrapElectronLocalSyncFromJournal,
  setElectronDesktopCredentials,
  startLocalWatchers,
} from '../workspace/connectors/utils/electron-local-sync';

export function ElectronLocalSyncBootstrap() {
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const hasRunRef = useRef(false);

  useEffect(() => {
    if (!isElectron()) return;
    if (hasRunRef.current) return;
    if (!isHydrated || !isAuthenticated || !refreshToken) return;

    hasRunRef.current = true;
    setElectronDesktopCredentials()
      .catch((error) => {
        console.warn('[local-sync] could not hand credentials to the desktop:', error);
        hasRunRef.current = false;
        // Rethrow so the chain below never reaches `startLocalWatchers`.
        throw error;
      })
      .then(() =>
        startLocalWatchers().catch((error) => {
          console.warn('[local-sync] watcher bootstrap failed, falling back to journal:', error);
          return bootstrapElectronLocalSyncFromJournal().catch((fallbackError) => {
            console.warn('[local-sync] bootstrap from journal failed:', fallbackError);
            hasRunRef.current = false;
          });
        })
      )
      .catch(() => {
        // Credential handoff already logged + reset hasRunRef above; swallow
        // here so this doesn't surface as an unhandled rejection.
      });
  }, [isHydrated, isAuthenticated, refreshToken]);

  return null;
}
