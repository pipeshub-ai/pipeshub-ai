import { useCallback, useEffect, useState } from 'react';
import { isElectron } from './is-electron';
import { forgetElectronServerUrl } from './api-base-url-storage';

/** Mirrors electron/local-server/launcher-client.ts (separate builds, so the shape is repeated). */
export type LocalServerErrorCode =
  | 'launcher_missing'
  | 'spawn_failed'
  | 'port_in_use'
  | 'crashed'
  | 'timeout'
  | 'bad_url';

export interface LocalServerStatus {
  state: 'idle' | 'starting' | 'running' | 'stopping' | 'failed';
  phase?: string;
  url?: string;
  error?: { code: LocalServerErrorCode; message: string; logTail: string[] };
}

export type DesktopMode = 'local' | 'remote';

export interface DesktopState {
  /** null until the first-run choice is made. */
  mode: DesktopMode | null;
  keepRunningInTray: boolean;
  /** False when this build carries no local server (the choice is then disabled). */
  localAvailable: boolean;
  local: LocalServerStatus;
  dataDir: string;
}

export interface LocalRepoSummary {
  repoKey: string;
  name: string;
  rootPath: string;
  state: string;
  files: number;
}

export interface DesktopApi {
  getState(): Promise<DesktopState>;
  setMode(mode: DesktopMode): Promise<DesktopState>;
  setKeepRunning(value: boolean): Promise<DesktopState>;
  retryLocal(): Promise<DesktopState>;
  openLogs(): Promise<unknown>;
  onStateChanged(callback: (state: DesktopState) => void): () => void;
}

export interface LocalRepoApi {
  add(rootPath: string): Promise<{ ok: boolean; error?: string; repo?: LocalRepoSummary }>;
  list(): Promise<LocalRepoSummary[]>;
  remove(repoKey: string): Promise<{ ok: boolean; error?: string }>;
}

function electronApi(): { desktop?: DesktopApi; localRepo?: LocalRepoApi; selectFolder?: () => Promise<string | null> } | undefined {
  if (typeof window === 'undefined') return undefined;
  return (window as unknown as { electronAPI?: ReturnType<typeof electronApi> }).electronAPI;
}

export function getDesktopApi(): DesktopApi | undefined {
  return isElectron() ? electronApi()?.desktop : undefined;
}

export function getLocalRepoApi(): LocalRepoApi | undefined {
  return isElectron() ? electronApi()?.localRepo : undefined;
}

export function selectFolder(): Promise<string | null> {
  return electronApi()?.selectFolder?.() ?? Promise.resolve(null);
}

/** What the app should show before the signed-in UI. */
export type DesktopGate = 'ready' | 'choose' | 'setup' | 'local';

/**
 * Pure routing rule for the first screen of the desktop app.
 * `skipServerSetup` is the existing "a server URL is already configured" check.
 */
export function decideDesktopGate(
  state: Pick<DesktopState, 'mode'> | null,
  skipServerSetup: boolean,
): DesktopGate {
  if (state?.mode === 'local') return 'local';
  if (skipServerSetup) return 'ready';
  // No desktop bridge (older shell) behaves exactly as before: ask for a server URL.
  return state && state.mode === null ? 'choose' : 'setup';
}

/** Desktop state from main, kept fresh by its change events. `loaded` is true immediately outside Electron. */
export function useDesktopState(): { loaded: boolean; state: DesktopState | null; refresh: () => Promise<DesktopState | null> } {
  const api = getDesktopApi();
  const [state, setState] = useState<DesktopState | null>(null);
  const [loaded, setLoaded] = useState(!api);

  const refresh = useCallback(async () => {
    const next = (await getDesktopApi()?.getState()) ?? null;
    setState(next);
    setLoaded(true);
    return next;
  }, []);

  useEffect(() => {
    const desktop = getDesktopApi();
    if (!desktop) {
      setLoaded(true);
      return;
    }
    void refresh();
    return desktop.onStateChanged((next) => setState(next));
  }, [refresh]);

  return { loaded, state, refresh };
}

/**
 * Change between running here and connecting to a server. Tokens and the saved
 * URL belong to the other mode's server, so both are dropped first.
 */
export async function switchDesktopMode(mode: DesktopMode, signOut: () => void): Promise<void> {
  signOut();
  forgetElectronServerUrl();
  await getDesktopApi()?.setMode(mode);
}
