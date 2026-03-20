import { Router } from 'express';
import { Container } from 'inversify';
import { CrawlingSchedulerService } from '../services/crawling_service';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { ConnectorTypeSchema, CrawlingScheduleRequestSchema } from '../validator/validator';
import {
  getCrawlingJobStatus,
  removeCrawlingJob,
  scheduleCrawlingJob,
  getAllCrawlingJobStatus,
  removeAllCrawlingJob,
  pauseCrawlingJob,
  resumeCrawlingJob,
  getQueueStats,
} from '../controller/cm_controller';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

export function createCrawlingManagerRouter(container: Container): Router {
  const router = Router();
  const crawlingService = container.get<CrawlingSchedulerService>(
    CrawlingSchedulerService,
  );
  const appConfig = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>(AuthMiddleware);
  // POST /api/v1/crawlingManager/:connectorType/schedule - Schedule a crawling job
  router.post(
    '/:connector/:connectorId/schedule',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(CrawlingScheduleRequestSchema),
    scheduleCrawlingJob(crawlingService, appConfig),
  );

  // GET /api/v1/crawlingManager/:connectorType/schedule - Get job status for specific connector
  router.get(
    '/:connector/:connectorId/schedule',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_READ),
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    getCrawlingJobStatus(crawlingService, appConfig),
  );

  // GET /api/v1/crawlingManager/schedule/all - Get all job statuses for organization
  router.get(
    '/schedule/all',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_READ),
    metricsMiddleware(container),
    getAllCrawlingJobStatus(crawlingService),
  );

  // DELETE /api/v1/crawlingManager/schedule/all - Remove all jobs for organization
  router.delete(
    '/schedule/all',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_DELETE),
    metricsMiddleware(container),
    removeAllCrawlingJob(crawlingService),
  );

  // DELETE /api/v1/crawlingManager/:connectorType/schedule - Remove specific job
  router.delete(
    '/:connector/:connectorId/remove',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_DELETE),
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    removeCrawlingJob(crawlingService, appConfig),
  );

  // POST /api/v1/crawlingManager/:connectorType/pause - Pause a specific job
  router.post(
    '/:connector/:connectorId/pause',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_WRITE),
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    pauseCrawlingJob(crawlingService, appConfig),
  );

  // POST /api/v1/crawlingManager/:connectorType/resume - Resume a specific job
  router.post(
    '/:connector/:connectorId/resume',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_WRITE),
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    resumeCrawlingJob(crawlingService, appConfig),
  );

  // GET /api/v1/crawlingManager/stats - Get queue statistics
  router.get(
    '/stats',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CRAWL_READ),
    metricsMiddleware(container),
    getQueueStats(crawlingService),
  );

  return router;
}

export default createCrawlingManagerRouter;
