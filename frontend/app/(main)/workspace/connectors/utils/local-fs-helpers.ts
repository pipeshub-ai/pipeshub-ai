import {
  LOCAL_FS_DESKTOP_OFFLINE,
  LOCAL_FS_DESKTOP_OFFLINE_TOAST_DURATION_MS,
  LOCAL_FS_DESKTOP_OFFLINE_TOAST_TITLE,
  LOCAL_FS_DESKTOP_UNCLAIMED,
  LOCAL_FS_DESKTOP_UNCLAIMED_TOAST_TITLE,
} from '../constants';

/** Why Node refused to start a Local FS sync. Mirrors the 409 `details.code`. */
export type DesktopRefusalReason = 'offline' | 'unclaimed';

/**
 * Check if a connector type string identifies a Local FS connector.
 * Matches the backend identifiers: LOCAL_FS, local-fs, localfs, localfilesystem.
 */
export function isLocalFsConnectorType(connectorType: string): boolean {
  const normalized = connectorType.trim().replace(/[-_\s]+/g, '').toLowerCase();
  return (
    normalized === 'localfs' ||
    normalized === 'localfilesystem'
  );
}

/**
 * Node answers 409 with `details.code` set to one of the desktop codes.
 * Matched on the code, not on 409 — the resync route also 409s for "a sync
 * is already running", and treating that as an offline desktop would tell
 * the user the opposite of what happened. The axios interceptor exposes
 * only `message` and `details` of the body.
 */
export function readDesktopRefusal(error: unknown): DesktopRefusalReason | null {
  const code = (error as { details?: { code?: string } } | null | undefined)?.details?.code;
  if (code === LOCAL_FS_DESKTOP_OFFLINE) return 'offline';
  if (code === LOCAL_FS_DESKTOP_UNCLAIMED) return 'unclaimed';
  return null;
}

/**
 * Toast-suppression predicate for `api.ts` (kept here, not in
 * `connector-sync-actions`, to avoid an import cycle). True for every
 * refusal the callers render themselves as an info toast.
 */
export function isDesktopOfflineError(error: unknown): boolean {
  return readDesktopRefusal(error) !== null;
}

/** The info toast for a `requires-desktop` outcome. One home for the wording. */
export function localFsDesktopToast(outcome: { reason: DesktopRefusalReason }): {
  variant: 'info';
  title: string;
  duration: number;
} {
  return {
    variant: 'info',
    title:
      outcome.reason === 'unclaimed'
        ? LOCAL_FS_DESKTOP_UNCLAIMED_TOAST_TITLE
        : LOCAL_FS_DESKTOP_OFFLINE_TOAST_TITLE,
    duration: LOCAL_FS_DESKTOP_OFFLINE_TOAST_DURATION_MS,
  };
}
