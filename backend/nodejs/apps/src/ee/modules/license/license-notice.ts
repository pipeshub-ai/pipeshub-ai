import {
  licenseStatus,
  type LicenseStatusView,
  type LicenseStore,
} from './license.service';

/**
 * Written by the Python connectors (`licence_lift.publish_connector_states`): every company-wide
 * connector that copies access from its source, with its access limit, as
 * `{ connectors: StoredConnector[] }`. Storage and database connectors are never in it.
 */
export const LICENSE_CONNECTORS_KEY = '/services/licence/connectors';

/** As read back: written by the Python connectors, so each field is checked before use. */
interface StoredConnector {
  id: string;
  orgId: string;
  name?: string | null;
  type?: string | null;
  createdBy?: string | null;
  /** Set while the connector is limited (`CREATOR` or `ORG`); null while it copies access as before. */
  accessLimit?: string | null;
  /** Its sync status (`QUEUED`, `SYNCING`, `FULL_SYNCING`, `IDLE`, ...). */
  status?: string | null;
}

/**
 * - `affected`: will be limited when the upgrade grace ends; listed only during the grace.
 * - `limited`: limited to its creator, as permission sync is not allowed.
 * - `restoring`: still limited, but a key now allows permission sync: the full sync that lifts it is owed or running.
 */
export type ConnectorNoticeState = 'affected' | 'limited' | 'restoring';

export interface ConnectorNotice {
  id: string;
  name: string;
  type: string;
  state: ConnectorNoticeState;
  status: string | null;
}

/** What every signed-in user may read about the license: no file, no signature, no licensee. */
export interface LicenseNotice {
  /** Set while no license key has started and the upgrade grace runs. */
  upgradeGrace: { endsAt: string } | null;
  /** Whether connectors may copy access from their sources now; true while nothing is enforced. */
  permissionSync: boolean;
  /** A development key is in force. */
  notForProduction: boolean;
  /**
   * Limited and restoring connectors. For a member, only those created by someone else (the ones
   * that drop out of their results), and never their names: `items` is null.
   */
  connectors: {
    limited: number;
    restoring: number;
    items: ConnectorNotice[] | null;
  };
}

export interface NoticeViewer {
  userId: string;
  orgId: string;
  isAdmin: boolean;
}

const IN_FORCE = new Set(['active', 'expiring', 'grace']);

/** When the upgrade grace ends, while it runs and no license key has started; else null. */
export function upgradeGraceEndsAt(
  view: LicenseStatusView,
  now: Date,
): string | null {
  const endsAt = view.upgradeGrace?.endsAt;
  const noLicense = view.status === 'none' || view.status === 'pending';
  return noLicense &&
    endsAt !== undefined &&
    endsAt !== null &&
    now.getTime() < Date.parse(endsAt)
    ? endsAt
    : null;
}

const storedConnectors = async (
  store: LicenseStore,
): Promise<StoredConnector[]> => {
  const raw = (await store.read(LICENSE_CONNECTORS_KEY)) as {
    connectors?: unknown;
  } | null;
  return Array.isArray(raw?.connectors)
    ? (raw.connectors as Array<Partial<StoredConnector> | null>).filter(
        (c): c is StoredConnector =>
          typeof c?.id === 'string' && typeof c.orgId === 'string',
      )
    : [];
};

export async function licenseNotice(
  store: LicenseStore,
  viewer: NoticeViewer,
  now: Date = new Date(),
): Promise<LicenseNotice> {
  const view = await licenseStatus(store, now);
  const graceEndsAt = upgradeGraceEndsAt(view, now);
  const permissionSync = view.permissionSync?.allowed ?? true;

  const states = (await storedConnectors(store))
    .filter((c) => c.orgId === viewer.orgId)
    .flatMap((c) => {
      const state: ConnectorNoticeState | null =
        c.accessLimit != null
          ? permissionSync
            ? 'restoring'
            : 'limited'
          : graceEndsAt !== null
            ? 'affected'
            : null;
      return state === null ? [] : [{ stored: c, state }];
    });
  // A member's own limited connector stays in their results; only the others make them shrink.
  const counted = viewer.isAdmin
    ? states
    : states.filter(({ stored }) => stored.createdBy !== viewer.userId);
  const count = (state: ConnectorNoticeState): number =>
    counted.filter((entry) => entry.state === state).length;

  return {
    upgradeGrace: graceEndsAt === null ? null : { endsAt: graceEndsAt },
    permissionSync,
    notForProduction:
      IN_FORCE.has(view.status) && view.license?.notForProduction === true,
    connectors: {
      limited: count('limited'),
      restoring: count('restoring'),
      items: viewer.isAdmin
        ? states.map(({ stored, state }) => ({
            id: stored.id,
            name: stored.name ?? '',
            type: stored.type ?? '',
            state,
            status: stored.status ?? null,
          }))
        : null,
    },
  };
}

/** Warnings for the startup log: a development key, and the upgrade grace with its end. */
export function startupWarnings(view: LicenseStatusView, now: Date): string[] {
  const warnings: string[] = [];
  if (IN_FORCE.has(view.status) && view.license?.notForProduction === true) {
    warnings.push(
      'A development license key is in force: this installation is not for production use.',
    );
  }
  const graceEndsAt = upgradeGraceEndsAt(view, now);
  if (graceEndsAt !== null) {
    warnings.push(
      `No license key is installed. Company-wide connectors keep copying access from their sources until the upgrade grace ends at ${graceEndsAt}; after that they are limited to their creators until a key is installed.`,
    );
  }
  return warnings;
}
