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
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';

export function createCrawlingManagerRouter(container: Container): Router {
  const router = Router();
  const crawlingService = container.get<CrawlingSchedulerService>(
    CrawlingSchedulerService,
  );
  const authMiddleware = container.get<AuthMiddleware>(AuthMiddleware);
  // POST /api/v1/crawlingManager/:connectorType/schedule - Schedule a crawling job
  router.post(
    '/:connector/schedule',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(CrawlingScheduleRequestSchema),
    scheduleCrawlingJob(crawlingService),
  );

  // GET /api/v1/crawlingManager/:connectorType/schedule - Get job status for specific connector
  router.get(
    '/:connector/schedule',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    getCrawlingJobStatus(crawlingService),
  );

  // GET /api/v1/crawlingManager/schedule/all - Get all job statuses for organization
  router.get(
    '/schedule/all',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getAllCrawlingJobStatus(crawlingService),
  );

  // DELETE /api/v1/crawlingManager/schedule/all - Remove all jobs for organization
  router.delete(
    '/schedule/all',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    removeAllCrawlingJob(crawlingService),
  );

  // DELETE /api/v1/crawlingManager/:connectorType/schedule - Remove specific job
  router.delete(
    '/:connector/schedule',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    removeCrawlingJob(crawlingService),
  );

  // POST /api/v1/crawlingManager/:connectorType/pause - Pause a specific job
  router.post(
    '/:connector/pause',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    pauseCrawlingJob(crawlingService),
  );

  // POST /api/v1/crawlingManager/:connectorType/resume - Resume a specific job
  router.post(
    '/:connector/resume',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ConnectorTypeSchema),
    metricsMiddleware(container),
    resumeCrawlingJob(crawlingService),
  );

  // GET /api/v1/crawlingManager/stats - Get queue statistics
  router.get(
    '/stats',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getQueueStats(crawlingService),
  );

  return router;
}

export default createCrawlingManagerRouter;