import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { CrawlingSchedulerService } from '../services/crawling_service';

export const scheduleCrawlingJob =
  (crawlingService: CrawlingSchedulerService) =>
  (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connectorType, scheduleConfig } = req.body;
    const { userId, orgId } = req.user as { userId: string; orgId: string };
    try {
      const job = crawlingService.scheduleJob(
        connectorType,
        scheduleConfig,
        orgId,
        userId,
      );

      res
        .status(200)
        .json({ message: 'Crawling job scheduled successfully', job });
    } catch (error) {
      next(error);
    }
  };
