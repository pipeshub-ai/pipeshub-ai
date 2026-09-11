import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { Response } from 'express';
import { isLocalFsConnector } from '../../../utils/local-fs-utils';
import {
  DesktopPresence,
  resolveDesktopPresence,
} from '../../../libs/services/desktop-presence.provider';

export const DESKTOP_OFFLINE_CODE = 'DESKTOP_OFFLINE';
/** A desktop is connected but has never claimed this connector (first enable). */
export const DESKTOP_UNCLAIMED_CODE = 'DESKTOP_UNCLAIMED';

export type DesktopRefusalReason = 'offline' | 'unclaimed';

const logger = Logger.getInstance({
  service: 'Connector Utils',
});

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

/**
 * FastAPI validation errors (422) send `detail` as an array of
 * `{loc, msg, type}` objects rather than a string. Stringifying that array
 * directly (e.g. in a template literal) yields "[object Object]" since
 * Array.prototype.toString calls the default Object.toString on each entry.
 * This extracts a readable message instead.
 */
const stringifyErrorDetail = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) =>
        entry && typeof entry === 'object' && 'msg' in entry
          ? String((entry as { msg: unknown }).msg)
          : JSON.stringify(entry),
      )
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail);
  }
  return 'Unknown error';
};

export const handleBackendError = (error: any, operation: string): Error => {
  if (error) {
    if (
      (error?.cause && error.cause.code === 'ECONNREFUSED') ||
      (typeof error?.message === 'string' &&
        error.message.includes('fetch failed'))
    ) {
      return new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    const { statusCode, data, message } = error;
    const errorDetail = stringifyErrorDetail(
      data?.detail || data?.reason || data?.message || message || 'Unknown error',
    );

    logger.error(`Backend error during ${operation}`, {
      statusCode,
      errorDetail,
      fullResponse: data,
    });

    if (errorDetail === 'ECONNREFUSED') {
      throw new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    switch (statusCode) {
      case 400:
        return new BadRequestError(errorDetail);
      case 401:
        return new UnauthorizedError(errorDetail);
      case 403:
        return new ForbiddenError(errorDetail);
      case 404:
        return new NotFoundError(errorDetail);
      case 409:
        return new ConflictError(errorDetail);
      case 422:
        return new BadRequestError(errorDetail);
      case 500:
        return new InternalServerError(errorDetail);
      default:
        return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    logger.error(`No response from backend during ${operation}`);
    return new InternalServerError('Backend service unavailable');
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

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

type PresenceRow = {
  _key?: string;
  type?: string;
  createdBy?: string;
  isActive?: boolean;
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
  // The desktop only claims connectors it has mounted a watcher for, which
  // happens on enable. Before that, "no claim" is expected, not "offline".
  if (instance.isActive !== true) return;
  const online = presence.isLocalFsDesktopOnline(
    orgId,
    instance.createdBy,
    instance._key,
  );
  if (online !== null) instance.desktopOnline = online;
};

/**
 * Stamp `desktopOnline` on sync-enabled Local FS rows of a Python instance
 * response (`connector` or `connectors`). Computed from the socket claim map
 * at response time, never persisted; left absent when presence is unknown or
 * the connector is not enabled.
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
  { code: string; message: (connectorId: string) => string }
> = {
  offline: {
    code: DESKTOP_OFFLINE_CODE,
    message: (connectorId) =>
      `No desktop is connected for connector ${connectorId}. ` +
      'Open the Pipeshub desktop app on the machine that owns this folder.',
  },
  unclaimed: {
    code: DESKTOP_UNCLAIMED_CODE,
    message: (connectorId) =>
      `Connector ${connectorId} has not been set up on a desktop yet. ` +
      'Open the Pipeshub desktop app on the machine that owns this folder ' +
      'and enable sync there once.',
  },
};

/**
 * Written directly rather than via `next(error)`: the error middleware fixes
 * `code` per error class, and the frontend only sees `message` + `details`.
 */
export const respondLocalFsDesktopRefusal = (
  res: Response,
  connectorId: string,
  reason: DesktopRefusalReason = 'offline',
): void => {
  const { code, message } = DESKTOP_REFUSAL[reason];
  res.status(409).json({
    success: false,
    code,
    message: message(connectorId),
    details: { code, connectorId, retryable: true },
  });
};
