import { Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { AuthenticatedServiceRequest } from '../../../libs/middlewares/types';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { Logger } from '../../../libs/services/logger.service';
import { DesktopProxySocketGateway } from '../socket/desktop-proxy.gateway';
import {
  DesktopOfflineError,
  DesktopRemoteError,
  DesktopTimeoutError,
  LocalFsFetchContentPayload,
  LocalFsPullRequestPayload,
} from '../types/local-fs.types';

const logger = Logger.getInstance({ service: 'DesktopProxyController' });

/**
 * Shared error mapping for both routes. Transport outcomes are HTTP statuses;
 * the 200 body is the desktop's answer. Returns false when the error is not a
 * desktop transport outcome and belongs to the error middleware instead.
 */
function respondToDesktopError(res: Response, error: unknown): boolean {
  if (error instanceof DesktopOfflineError) {
    res.status(HTTP_STATUS.CONFLICT).json({
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
    res.status(HTTP_STATUS.GATEWAY_TIMEOUT).json({
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
    res.status(HTTP_STATUS.BAD_GATEWAY).json({
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

type DesktopTarget = {
  orgId: string;
  userId: string;
  gateway: DesktopProxySocketGateway;
};

/**
 * Resolves the desktop this request addresses, or writes the refusal and
 * returns null. The gateway is resolved per request, not at router
 * construction: the singleton exists before routes are mounted, but its
 * namespace is only attached once the HTTP server is listening.
 */
function resolveDesktopTargetOrRespond(
  container: Container,
  req: AuthenticatedServiceRequest,
  res: Response,
): DesktopTarget | null {
  const orgId = String(req.tokenPayload?.orgId ?? '');
  const userId = String(req.tokenPayload?.userId ?? '');
  if (!orgId || !userId) {
    res.status(HTTP_STATUS.BAD_REQUEST).json({
      success: false,
      code: 'NO_TARGET',
      error: {
        code: 'NO_TARGET',
        message: 'Unable to resolve a target desktop for this request',
        retryable: false,
      },
    });
    return null;
  }

  const gateway = container.get<DesktopProxySocketGateway>(
    DesktopProxySocketGateway,
  );
  if (!gateway.isReady()) {
    res.status(HTTP_STATUS.SERVICE_UNAVAILABLE).json({
      success: false,
      code: 'GATEWAY_NOT_READY',
      error: {
        code: 'GATEWAY_NOT_READY',
        message: 'Desktop relay is not accepting requests yet',
        retryable: true,
      },
    });
    return null;
  }

  return { orgId, userId, gateway };
}

export const pullLocalFsFileEvents =
  (container: Container) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const target = resolveDesktopTargetOrRespond(container, req, res);
      if (!target) return;

      const payload = req.body as LocalFsPullRequestPayload;
      const result = await target.gateway.requestLocalFsFileEvents(
        target.orgId,
        target.userId,
        payload.connectorId,
        payload,
      );
      res.status(HTTP_STATUS.OK).json({ success: true, data: result });
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
      const target = resolveDesktopTargetOrRespond(container, req, res);
      if (!target) return;

      const payload = req.body as LocalFsFetchContentPayload;
      const content = await target.gateway.requestLocalFsContent(
        target.orgId,
        target.userId,
        payload.connectorId,
        payload,
      );
      // Raw bytes, not JSON, so chunked streaming can be added later without
      // changing this contract or the Python client.
      res.status(HTTP_STATUS.OK).type('application/octet-stream').send(content);
    } catch (error) {
      if (respondToDesktopError(res, error)) return;
      next(error);
    }
  };
