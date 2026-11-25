import { NextFunction, Request, Response } from 'express';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { ServiceUnavailableError } from '../../../libs/errors/http.errors';
import type { HarnessLogger } from '../application/harness-log';
import {
  harnessLogMeta,
  silentHarnessLogger,
} from '../application/harness-log';
import type { LiveLayerService } from '../application/live-layer-service';
import { readLiveLayerSchema } from './schemas';
import { asWorkerNamedOrg } from './internal-auth';

/**
 * `GET /internal/control-plane/orgs/:orgId/live` (CPD-6): the org's live
 * layer for a worker or gateway holding it. 204 when the org head is still
 * `since`. Anything unreadable is 503, never an empty layer: the holder
 * keeps what it has and fails closed when that is too old (NFR-33).
 */
export class AgentHarnessLiveLayerController {
  constructor(
    private readonly layers: LiveLayerService,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  read = async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const orgId = String(req.params['orgId']);
    try {
      const { since } = readLiveLayerSchema.shape.query.parse(req.query);
      const layer = await asWorkerNamedOrg(orgId, () =>
        this.layers.read(orgId, since),
      );
      res.setHeader('Cache-Control', 'no-store');
      if (layer === null) {
        res.status(HTTP_STATUS.NO_CONTENT).end();
        return;
      }
      res.status(HTTP_STATUS.OK).json(layer);
    } catch (error) {
      this.logger.warn(
        'cp.live.unavailable',
        harnessLogMeta('cp.live.unavailable', {
          orgId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      next(
        new ServiceUnavailableError(
          "The organization's live layer cannot be read",
        ),
      );
    }
  };
}
