import { Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { DesktopProxySocketGateway } from '../socket/desktop-proxy.gateway';
import {
  DesktopOfflineError,
  DesktopRemoteError,
  DesktopTimeoutError,
  LocalFsFetchContentPayload,
  LocalFsPullRequestPayload,
} from '../types/local-fs-pull.types';

const logger = Logger.getInstance({ service: 'DesktopProxyController' });

/** Transport outcomes are HTTP statuses; the 200 body is the desktop's answer. */
const STATUS_BAD_REQUEST = 400;
const STATUS_DESKTOP_OFFLINE = 409;
const STATUS_DESKTOP_TIMEOUT = 504;
const STATUS_GATEWAY_NOT_READY = 503;
const STATUS_DESKTOP_FAILED = 502;

/**
 * Shared error mapping for both routes. Returns false when the error is not a
 * desktop transport outcome and belongs to the error middleware instead.
 */
function respondToDesktopError(res: Response, error: unknown): boolean {
  if (error instanceof DesktopOfflineError) {
    res.status(STATUS_DESKTOP_OFFLINE).json({
      success: false,
      code: 'DESKTOP_OFFLINE',
      error: {
        code: 'DESKTOP_OFFLINE',
        message: error.message,
        retryable: true,
      },
    });
    return true;
  }
  if (error instanceof DesktopTimeoutError) {
    res.status(STATUS_DESKTOP_TIMEOUT).json({
      success: false,
      code: 'DESKTOP_TIMEOUT',
      error: {
        code: 'DESKTOP_TIMEOUT',
        message: error.message,
        retryable: true,
      },
    });
    return true;
  }
  // The desktop is reachable but said no. `retryable` is passed through
  // verbatim — the connector uses it to decide between backing off and
  // abandoning the run, and inventing a value here would break that.
  if (error instanceof DesktopRemoteError) {
    res.status(STATUS_DESKTOP_FAILED).json({
      success: false,
      code: error.code,
      error: {
        code: error.code,
        message: error.message,
        retryable: error.retryable,
      },
    });
    return true;
  }
  return false;
}

export const pullLocalFsFileEvents =
  (container: Container) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const orgId = String(req.tokenPayload?.orgId ?? '');
      const userId = String(req.tokenPayload?.userId ?? '');
      if (!orgId || !userId) {
        res.status(STATUS_BAD_REQUEST).json({
          success: false,
          code: 'NO_TARGET',
          error: {
            code: 'NO_TARGET',
            message: 'Token carries no orgId/userId to address a desktop with',
            retryable: false,
          },
        });
        return;
      }

      // Resolved per request, not at router construction: the gateway
      // singleton exists before routes are mounted, but its namespace is only
      // attached once the HTTP server is listening.
      const gateway = container.get<DesktopProxySocketGateway>(
        DesktopProxySocketGateway,
      );
      if (!gateway.isReady()) {
        res.status(STATUS_GATEWAY_NOT_READY).json({
          success: false,
          code: 'GATEWAY_NOT_READY',
        });
        return;
      }

      const payload = req.body as LocalFsPullRequestPayload;
      const result = await gateway.requestLocalFsFileEvents(
        orgId,
        userId,
        payload.connectorId,
        payload,
      );
      res.status(200).json({ success: true, data: result });
    } catch (error) {
      if (error instanceof DesktopOfflineError) {
        logger.debug('Local FS pull: no desktop connected', {
          connectorId: req.body?.connectorId,
        });
      }
      if (respondToDesktopError(res, error)) return;
      next(error);
    }
  };

export const fetchLocalFsContent =
  (container: Container) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const orgId = String(req.tokenPayload?.orgId ?? '');
      const userId = String(req.tokenPayload?.userId ?? '');
      if (!orgId || !userId) {
        res.status(STATUS_BAD_REQUEST).json({
          success: false,
          code: 'NO_TARGET',
          error: {
            code: 'NO_TARGET',
            message: 'Token carries no orgId/userId to address a desktop with',
            retryable: false,
          },
        });
        return;
      }

      const gateway = container.get<DesktopProxySocketGateway>(
        DesktopProxySocketGateway,
      );
      if (!gateway.isReady()) {
        res.status(STATUS_GATEWAY_NOT_READY).json({
          success: false,
          code: 'GATEWAY_NOT_READY',
        });
        return;
      }

      const payload = req.body as LocalFsFetchContentPayload;
      const content = await gateway.requestLocalFsContent(
        orgId,
        userId,
        payload.connectorId,
        payload,
      );
      // Raw bytes, not JSON, so chunked streaming can be added later without
      // changing this contract or the Python client.
      res.status(200).type('application/octet-stream').send(content);
    } catch (error) {
      if (respondToDesktopError(res, error)) return;
      next(error);
    }
  };
