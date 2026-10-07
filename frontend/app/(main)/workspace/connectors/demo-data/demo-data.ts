import type { Connector, ConnectorStatsResponse } from '../types';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';

/** Connector type registered by the bundled Acme Corp sample-data connector. */
export const DEMO_CONNECTOR_TYPE = 'Demo';

/**
 * Reserved domain of the sample accounts (Alice, Bob). Nobody can receive mail
 * on a `.example` domain, so every account on it came from the demo.
 */
export const DEMO_ACCOUNT_DOMAIN = 'acme-demo.example';

/**
 * The sample record only Acme's pricing committee can open, and a committee
 * member's account. Kept in step with the fixture by test_demo_fixture.py.
 */
export const RESTRICTED_RECORD_TITLE = 'Enterprise pricing strategy 2026';
export const RESTRICTED_RECORD_READER = `bob@${DEMO_ACCOUNT_DOMAIN}`;

/** How long "Keep for now" hides the removal notice. */
export const REMOVAL_NOTICE_SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;

const SNOOZE_KEY_PREFIX = 'pipeshub.demoData.removalNoticeSnoozedUntil.';

export function isDemoConnector(connector: Pick<Connector, 'type'>): boolean {
  return connector.type === DEMO_CONNECTOR_TYPE;
}

/** Instances that are still usable: have an id and are not being deleted. */
function isLive(connector: Connector): connector is Connector & { _key: string } {
  return !!connector._key && connector.status !== CONNECTOR_INSTANCE_STATUS.DELETING;
}

/**
 * Demo connector instances, enabled or not. Turning the connector off stops
 * syncing but leaves its records in search, so a disabled demo still needs its
 * badge and still counts for the removal notice.
 */
export function demoConnectorsIn(connectors: Connector[]): Connector[] {
  return connectors.filter((c) => isDemoConnector(c) && isLive(c));
}

/** Whether any of them is enabled, which is when the chat landing offers its questions. */
export function hasActiveDemo(demoConnectors: Connector[]): boolean {
  return demoConnectors.some((c) => c.isActive);
}

/**
 * Every other connector instance, active or not: a disabled connector keeps
 * the records it already indexed, and search does not skip disabled ones.
 */
export function otherConnectorsIn(connectors: Connector[]): Connector[] {
  return connectors.filter((c) => !isDemoConnector(c) && isLive(c));
}

/** Whether a connector has at least one record indexed and ready to answer from. */
export function hasIndexedRecords(stats: ConnectorStatsResponse['data'] | undefined): boolean {
  return (stats?.stats?.indexingStatus?.COMPLETED ?? 0) > 0;
}

export function isSampleAccountEmail(email: string | undefined | null): boolean {
  const normalized = (email ?? '').trim().toLowerCase();
  return normalized.endsWith(`@${DEMO_ACCOUNT_DOMAIN}`);
}

// ── "Keep for now" ───────────────────────────────────────────────────────
// Per browser, keyed by the Demo connector instance. localStorage can be
// missing or throw (private windows, blocked storage); the notice then simply
// shows again, which is the safe way to fail.

type SnoozeStorage = Pick<Storage, 'getItem' | 'setItem'>;

function defaultStorage(): SnoozeStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function isRemovalNoticeSnoozed(
  connectorId: string,
  now: number = Date.now(),
  storage: SnoozeStorage | null = defaultStorage(),
): boolean {
  try {
    const until = Number(storage?.getItem(SNOOZE_KEY_PREFIX + connectorId));
    return Number.isFinite(until) && until > now;
  } catch {
    return false;
  }
}

export function snoozeRemovalNotice(
  connectorId: string,
  now: number = Date.now(),
  storage: SnoozeStorage | null = defaultStorage(),
): void {
  try {
    storage?.setItem(SNOOZE_KEY_PREFIX + connectorId, String(now + REMOVAL_NOTICE_SNOOZE_MS));
  } catch {
    // Best effort only; see above.
  }
}
