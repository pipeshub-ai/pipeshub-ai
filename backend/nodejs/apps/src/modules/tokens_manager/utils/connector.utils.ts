import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { Response } from 'express';
import { isLocalFsConnector } from '../../../utils/local-fs-utils';
import {
  DesktopPresence,
  isLocalFsDeviceOnline,
  resolveDesktopPresence,
} from '../../../libs/services/desktop-presence.provider';
import { AppConfig } from '../config/config';

export const DESKTOP_OFFLINE_CODE = 'DESKTOP_OFFLINE';
/** No owner device yet: sync must first be enabled from the desktop app. */
export const DESKTOP_UNCLAIMED_CODE = 'DESKTOP_UNCLAIMED';
export const DESKTOP_OWNED_BY_OTHER_DEVICE_CODE = 'DESKTOP_OWNED_BY_OTHER_DEVICE';

export type DesktopRefusalReason = 'offline' | 'unclaimed' | 'other_device';

import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  handleBackendError as mapBackendError,
  retryAfterToSeconds,
  SERVICE_UNAVAILABLE_MESSAGE,
} from '../../../libs/errors/backend-error';

export { retryAfterToSeconds, SERVICE_UNAVAILABLE_MESSAGE };

/**
 * Shared with every other module that calls a PipesHub service, so one failed
 * call reads the same way wherever it happened.
 *
 * Kept as a function here rather than `export { … } from`: a re-export compiles
 * to a getter, and the suites for the modules that call this replace the
 * property with a stub.
 */
export const handleBackendError = (error: unknown, operation: string): Error =>
  mapBackendError(error, operation);

// Helper function to execute connector service commands
export const executeConnectorCommand = async (
  uri: string,
  method: HttpMethod,
  headers: Record<string, string>,
  body?: any,
) => {
  const connectorCommandOptions: ConnectorServiceCommandOptions = {
    uri,
    method,
    headers: {
      ...headers,
      // Lowercase — sanitizeHeaders normalizes keys; avoid a second
      // Content-Type that fetch would join into "application/json, application/json".
      'content-type': 'application/json',
    },
    ...(body && { body }),
  };
  const connectorCommand = new ConnectorServiceCommand(connectorCommandOptions);
  return await connectorCommand.execute();
};

// Helper function to handle common connector response logic
export const handleConnectorResponse = (
  connectorResponse: any,
  res: Response,
  operation: string,
  failureMessage: string,
) => {
  const statusCode = connectorResponse?.statusCode;
  const isSuccess = statusCode >= 200 && statusCode < 300;
  if (connectorResponse && !isSuccess) {
    throw handleBackendError(connectorResponse, operation);
  }
  const connectorsData = connectorResponse.data;
  if (!connectorsData) {
    throw new NotFoundError(`${operation} failed: ${failureMessage}`);
  }
  res.status(statusCode ?? 200).json(connectorsData);
};

export interface ConnectorInstanceSummary {
  _key?: string;
  type?: string;
  scope?: string;
  createdBy?: string;
  isActive?: boolean;
  isLocked?: boolean;
  status?: string;
  ownerDeviceId?: string | null;
  ownerDeviceName?: string | null;
}

/** Throws when Python answers non-200 or without a `connector` body. */
export const fetchConnectorInstanceSummary = async (
  connectorId: string,
  appConfig: AppConfig,
  headers: Record<string, string>,
): Promise<ConnectorInstanceSummary> => {
  const response = await executeConnectorCommand(
    `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
    HttpMethod.GET,
    headers,
  );

  const data = response.data as
    | { connector?: ConnectorInstanceSummary }
    | undefined;
  if (response.statusCode !== 200 || !data?.connector) {
    throw new InternalServerError(
      `Failed to fetch connector ${connectorId} state`,
    );
  }
  return data.connector;
};

/**
 * Why a sync on this Local FS connector must not start now, or `null` to let
 * it through. Presence is keyed on createdBy: the desktop registers under its
 * owner's userId, which may differ from an admin caller.
 *
 * `requestDeviceId` is the caller's desktop on toggle-on, the only path that
 * may claim an unowned connector.
 */
export const localFsSyncRefusal = (
  orgId: string | undefined,
  instance: ConnectorInstanceSummary | null,
  options: { fallbackUserId?: string; requestDeviceId?: string } = {},
): DesktopRefusalReason | null => {
  if (!instance || !isLocalFsConnector(String(instance.type ?? ''))) {
    return null;
  }
  const userId = instance.createdBy ?? options.fallbackUserId;
  if (!orgId || !userId) return null;

  const ownerDeviceId = instance.ownerDeviceId || null;
  const requestDeviceId = options.requestDeviceId || null;
  if (ownerDeviceId) {
    if (requestDeviceId && requestDeviceId !== ownerDeviceId) {
      return 'other_device';
    }
    return isLocalFsDeviceOnline(orgId, userId, ownerDeviceId) === false
      ? 'offline'
      : null;
  }
  if (!requestDeviceId) return 'unclaimed';
  return isLocalFsDeviceOnline(orgId, userId, requestDeviceId) === false
    ? 'offline'
    : null;
};

type PresenceRow = {
  _key?: string;
  type?: string;
  createdBy?: string;
  isActive?: boolean;
  ownerDeviceId?: string | null;
  desktopOnline?: boolean;
};

const annotateRow = (
  row: unknown,
  orgId: string,
  presence: DesktopPresence,
): void => {
  if (!row || typeof row !== 'object') return;
  const instance = row as PresenceRow;
  // The desktop registers under its owner's userId, which for a personal
  // connector is createdBy — not necessarily the caller (an admin may list it).
  if (!instance._key || !instance.createdBy) return;
  if (!isLocalFsConnector(String(instance.type ?? ''))) return;
  // Before the first enable there is no owner device to be offline.
  if (instance.isActive !== true || !instance.ownerDeviceId) return;
  const online = presence.isLocalFsDeviceOnline(
    orgId,
    instance.createdBy,
    instance.ownerDeviceId,
  );
  if (online !== null) instance.desktopOnline = online;
};

/**
 * Stamp `desktopOnline` on sync-enabled Local FS rows of a Python instance
 * response (`connector` or `connectors`). Reflects whether the owner device
 * has a socket at response time, never persisted; left absent when presence
 * is unknown, the connector is not enabled, or it has no owner device.
 */
export const annotateLocalFsDesktopPresence = (
  body: unknown,
  orgId: string | undefined,
  presence: DesktopPresence | null = resolveDesktopPresence(),
): void => {
  if (!body || typeof body !== 'object' || !orgId || !presence) return;
  const data = body as { connector?: unknown; connectors?: unknown };
  annotateRow(data.connector, orgId, presence);
  if (Array.isArray(data.connectors)) {
    for (const row of data.connectors) annotateRow(row, orgId, presence);
  }
};

const DESKTOP_REFUSAL: Record<
  DesktopRefusalReason,
  {
    code: string;
    message: (connectorId: string, ownerDeviceName?: string | null) => string;
  }
> = {
  offline: {
    code: DESKTOP_OFFLINE_CODE,
    message: (connectorId, ownerDeviceName) =>
      ownerDeviceName
        ? `Device "${ownerDeviceName}" that owns connector ${connectorId} is not connected. ` +
          'Open the Pipeshub desktop app on that machine.'
        : `No desktop is connected for connector ${connectorId}. ` +
          'Open the Pipeshub desktop app on the machine that owns this folder.',
  },
  unclaimed: {
    code: DESKTOP_UNCLAIMED_CODE,
    message: (connectorId) =>
      `Connector ${connectorId} has not been set up on a desktop yet. ` +
      'Open the Pipeshub desktop app on the machine that owns this folder ' +
      'and enable sync there once.',
  },
  other_device: {
    code: DESKTOP_OWNED_BY_OTHER_DEVICE_CODE,
    message: (connectorId, ownerDeviceName) =>
      `Connector ${connectorId} is owned by ` +
      `${ownerDeviceName ? `"${ownerDeviceName}"` : 'another device'}. ` +
      'Enable sync from the Pipeshub desktop app on that machine.',
  },
};

// A Map, not an object literal: a detail leading with an inherited key
// ("constructor: ...") would otherwise resolve to a function and reach
// respondLocalFsDesktopRefusal as a reason it cannot look up.
const REFUSAL_REASON_BY_CODE = new Map<string, DesktopRefusalReason>([
  [DESKTOP_OFFLINE_CODE, 'offline'],
  [DESKTOP_UNCLAIMED_CODE, 'unclaimed'],
  [DESKTOP_OWNED_BY_OTHER_DEVICE_CODE, 'other_device'],
]);

/**
 * Python raises these as a 409 whose `detail` leads with the code
 * (`"DESKTOP_UNCLAIMED: ..."`); `handleBackendError` would flatten that into a
 * plain ConflictError the client cannot branch on.
 */
export const localFsRefusalFromBackend = (
  response: { statusCode?: number; data?: unknown } | null | undefined,
): DesktopRefusalReason | null => {
  if (response?.statusCode !== 409) return null;
  const detail = (response.data as { detail?: unknown } | undefined)?.detail;
  let code: string | undefined;
  if (typeof detail === 'string') {
    code = detail.split(':', 1)[0]?.trim();
  } else if (detail && typeof detail === 'object') {
    const value = (detail as { code?: unknown }).code;
    code = typeof value === 'string' ? value : undefined;
  }
  return (code && REFUSAL_REASON_BY_CODE.get(code)) || null;
};

/**
 * Written directly rather than via `next(error)`: the error middleware fixes
 * `code` per error class, and the frontend only sees `message` + `details`.
 */
export const respondLocalFsDesktopRefusal = (
  res: Response,
  connectorId: string,
  reason: DesktopRefusalReason = 'offline',
  ownerDeviceName?: string | null,
): void => {
  const { code, message } = DESKTOP_REFUSAL[reason];
  res.status(409).json({
    success: false,
    code,
    message: message(connectorId, ownerDeviceName),
    details: {
      code,
      connectorId,
      retryable: true,
      ...(ownerDeviceName ? { ownerDeviceName } : {}),
    },
  });
};
