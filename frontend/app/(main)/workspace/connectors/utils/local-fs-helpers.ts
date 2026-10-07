import { i18n } from '@/lib/i18n';
import { isElectron } from '@/lib/electron';
import {
  LOCAL_FS_DESKTOP_OFFLINE,
  LOCAL_FS_DESKTOP_OFFLINE_TOAST_DURATION_MS,
  LOCAL_FS_DESKTOP_OWNED_BY_OTHER_DEVICE,
  LOCAL_FS_DESKTOP_UNCLAIMED,
} from '../constants';

/** Why Node refused to start a Local FS sync. Mirrors the 409 `details.code`. */
export type DesktopRefusalReason = 'offline' | 'unclaimed' | 'other_device';

export interface DesktopRefusal {
  reason: DesktopRefusalReason;
  /** Present when the connector has an owner device and Node sent its name. */
  ownerDeviceName?: string;
}

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
 * Local FS settings live on the user's machine and are applied by the desktop
 * watcher, so the browser can only show them. Callers grey out the editors
 * rather than hiding them, so a user can still read the current setup.
 */
export function isLocalFsConfigReadOnly(connectorType: string): boolean {
  return isLocalFsConnectorType(connectorType) && !isElectron();
}

const REASON_BY_CODE = new Map<string, DesktopRefusalReason>([
  [LOCAL_FS_DESKTOP_OFFLINE, 'offline'],
  [LOCAL_FS_DESKTOP_UNCLAIMED, 'unclaimed'],
  [LOCAL_FS_DESKTOP_OWNED_BY_OTHER_DEVICE, 'other_device'],
]);

/**
 * Node answers 409 with `details.code` set to one of the desktop codes.
 * Matched on the code, not on 409 — the resync route also 409s for "a sync
 * is already running", and treating that as an offline desktop would tell
 * the user the opposite of what happened. The axios interceptor exposes
 * only `message` and `details` of the body.
 */
export function readDesktopRefusal(error: unknown): DesktopRefusal | null {
  const details = (
    error as { details?: { code?: unknown; ownerDeviceName?: unknown } } | null | undefined
  )?.details;
  const reason = typeof details?.code === 'string' ? REASON_BY_CODE.get(details.code) : undefined;
  if (!reason) return null;
  const refusal: DesktopRefusal = { reason };
  const ownerDeviceName =
    typeof details?.ownerDeviceName === 'string' ? details.ownerDeviceName.trim() : '';
  if (ownerDeviceName) refusal.ownerDeviceName = ownerDeviceName;
  return refusal;
}

/**
 * Toast-suppression predicate for `api.ts` (kept here, not in
 * `connector-sync-actions`, to avoid an import cycle). True for every
 * refusal the callers render themselves as an info toast.
 */
export function isDesktopOfflineError(error: unknown): boolean {
  return readDesktopRefusal(error) !== null;
}

function desktopRefusalTitle(refusal: DesktopRefusal): string {
  switch (refusal.reason) {
    case 'unclaimed':
      return i18n.t('workspace.connectors.localFsDesktop.unclaimedToast');
    case 'other_device':
      return refusal.ownerDeviceName
        ? i18n.t('workspace.connectors.localFsDesktop.ownedByOtherDeviceToast', {
            deviceName: refusal.ownerDeviceName,
          })
        : i18n.t('workspace.connectors.localFsDesktop.ownedByOtherDeviceUnknownToast');
    case 'offline':
      return refusal.ownerDeviceName
        ? i18n.t('workspace.connectors.localFsDesktop.offlineNamedToast', {
            deviceName: refusal.ownerDeviceName,
          })
        : i18n.t('workspace.connectors.localFsDesktop.offlineToast');
  }
}

/** The info toast for a `requires-desktop` outcome. One home for the wording. */
export function localFsDesktopToast(outcome: DesktopRefusal): {
  variant: 'info';
  title: string;
  duration: number;
} {
  return {
    variant: 'info',
    title: desktopRefusalTitle(outcome),
    duration: LOCAL_FS_DESKTOP_OFFLINE_TOAST_DURATION_MS,
  };
}
