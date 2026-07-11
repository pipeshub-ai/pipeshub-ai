import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import axios from 'axios';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { isUserOrgAdmin } from '../../user_management/services/user-admin.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { iamUserLookupJwtGenerator } from '../../../libs/utils/createJwt';
import { ProgressService } from '../service/progress.service';

const logger = Logger.getInstance({ service: 'ProgressRoutes' });

/**
 * Ask the always-up query service to seed this org's Redis counters from DB
 * truth. Best-effort: a failure just means the REST call returns 204 until the
 * indexer's own seed/reconcile populates the counters.
 */
async function seedViaQueryService(
  aiBackend: string,
  orgId: string,
  serviceToken: string,
): Promise<void> {
  try {
    await axios.post(
      `${aiBackend}/api/v1/internal/progress/seed`,
      { orgId },
      {
        timeout: 5000,
        // The query service auth middleware validates this scoped service token;
        // without it the internal route 401s (it is not exempt from auth).
        headers: { Authorization: `Bearer ${serviceToken}` },
      },
    );
  } catch (error) {
    logger.warn('On-demand progress seed failed', {
      orgId,
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

export function createProgressRouter(
  progressContainer: Container,
  userManagerContainer: Container,
  appConfig: AppConfig,
): Router {
  const router = Router();
  const authMiddleware =
    userManagerContainer.get<AuthMiddleware>('AuthMiddleware');
  const auth = authMiddleware.authenticate.bind(authMiddleware);
  const progressService =
    progressContainer.get<ProgressService>(ProgressService);

  // Admin-only guard — returns 403 (not 400) for non-admins.
  const adminOnly = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      if (!userId || !orgId) {
        res.status(401).send();
        return;
      }
      if (!(await isUserOrgAdmin(userId, orgId))) {
        res.status(403).json({ message: 'Admin access required' });
        return;
      }
      next();
    } catch (error) {
      next(error);
    }
  };

  router.get(
    '/',
    auth,
    adminOnly,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const orgId = req.user!.orgId;
        let snapshot = await progressService.computeSnapshot(orgId);

        // Cold cache (e.g. indexer was down at widget open) — trigger an
        // on-demand seed on the query service, then recompute once.
        if (!snapshot && (await progressService.isEmpty(orgId))) {
          const serviceToken = iamUserLookupJwtGenerator(
            req.user!.userId,
            orgId,
            appConfig.scopedJwtSecret,
          );
          await seedViaQueryService(appConfig.aiBackend, orgId, serviceToken);
          snapshot = await progressService.computeSnapshot(orgId);
        }

        if (!snapshot) {
          res.status(204).send();
          return;
        }
        res.json(snapshot);
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
