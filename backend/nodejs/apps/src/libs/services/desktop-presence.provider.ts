/**
 * Cross-container access to "is this Local FS owner device connected". The socket gateway lives in the desktop-proxy container, which
 * is built after the crawling container whose services need the answer, so
 * consumers resolve it per call instead of via constructor injection. Same
 * pattern as `oauth-token-service.provider.ts`.
 */

export interface DesktopPresence {
  /** `null` when the gateway cannot tell yet (namespace not attached). */
  isLocalFsDeviceOnline(
    orgId: string,
    userId: string,
    deviceId: string,
  ): boolean | null;
  /** Any desktop of this user connected, registered device or not. Same `null` rule. */
  isDesktopConnected(orgId: string, userId: string): boolean | null;
}

let desktopPresenceInstance: DesktopPresence | null = null;

export function resolveDesktopPresence(): DesktopPresence | null {
  return desktopPresenceInstance;
}

/** Called once at startup after the gateway is resolved; `null` resets (tests). */
export function registerDesktopPresence(presence: DesktopPresence | null): void {
  desktopPresenceInstance = presence;
}

/**
 * Presence answer for a Local FS device, or `null` when unknown: no gateway
 * registered, or the gateway is not ready. Callers treat `null` as "let the
 * request through".
 */
export function isLocalFsDeviceOnline(
  orgId: string,
  userId: string,
  deviceId: string,
): boolean | null {
  const presence = resolveDesktopPresence();
  if (!presence) return null;
  return presence.isLocalFsDeviceOnline(orgId, userId, deviceId);
}

export function isDesktopConnected(
  orgId: string,
  userId: string,
): boolean | null {
  const presence = resolveDesktopPresence();
  if (!presence) return null;
  return presence.isDesktopConnected(orgId, userId);
}
