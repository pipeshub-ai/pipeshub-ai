'use client';

/**
 * ElectronLocalSyncBootstrap
 *
 * Mounts invisibly inside the (main) layout and does two things once the user
 * is signed in:
 *
 *  1. Hands the current access token to the main process and subscribes so
 *     every later refresh is pushed too. Main persists nothing and cannot mint
 *     tokens itself, so this is the only thing that lets it answer the
 *     server's pull. The initial push also re-arms main after a token expired
 *     while no window was open.
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
import { useAuthStore } from '@/config';
import {
  bootstrapElectronLocalSyncFromJournal,
  pushElectronDesktopAccessToken,
  startElectronDesktopTokenBridge,
  startLocalWatchers,
} from '../workspace/connectors/utils/electron-local-sync';

export function ElectronLocalSyncBootstrap() {
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const accessToken = useAuthStore((s) => s.accessToken);
  const hasRunRef = useRef(false);

  useEffect(() => {
    if (!isElectron()) return;
    if (hasRunRef.current) return;
    if (!isHydrated || !isAuthenticated || !accessToken) return;

    hasRunRef.current = true;
    startElectronDesktopTokenBridge();
    pushElectronDesktopAccessToken()
      .catch((error) => {
        console.warn('[local-sync] could not hand the access token to the desktop:', error);
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
        // Token handoff already logged + reset hasRunRef above; swallow
        // here so this doesn't surface as an unhandled rejection.
      });
  }, [isHydrated, isAuthenticated, accessToken]);

  return null;
}
