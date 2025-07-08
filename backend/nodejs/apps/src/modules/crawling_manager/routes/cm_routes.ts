import { Router } from 'express';
import { Container } from 'inversify';
import { CrawlingSchedulerService } from '../services/crawling_service';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { CrawlingScheduleRequestSchema } from '../validator/validator';
import { getJobStatus, removeJob, scheduleCrawlingJob } from '../controller/cm_controller';
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
    '/:connectorType/schedule',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(CrawlingScheduleRequestSchema),
    scheduleCrawlingJob(crawlingService),
  );

  router.get(
    '/:connectorType/schedule',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getJobStatus(crawlingService),
  );

  router.delete(
    '/:connectorType/schedule',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    removeJob(crawlingService),
  );

  return router;
}

export default createCrawlingManagerRouter;
