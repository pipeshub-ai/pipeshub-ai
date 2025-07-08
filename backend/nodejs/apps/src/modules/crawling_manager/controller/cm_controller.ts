import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { CrawlingSchedulerService } from '../services/crawling_service';
import { Logger } from '../../../libs/services/logger.service';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { CrawlingJobData } from '../schema/interface';
import { Job } from 'bullmq';
import { ConnectorType } from '../schema/enums';

const logger = Logger.getInstance({ service: 'CrawlingManagerController' });

export const scheduleCrawlingJob =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { scheduleConfig } = req.body;
    const { connectorType } = req.params as { connectorType: ConnectorType };
    const { userId, orgId } = req.user as { userId: string; orgId: string };
    try {
      logger.info('Scheduling crawling job', {
        connectorType,
        orgId,
        userId,
        scheduleType: scheduleConfig.scheduleType,
      });
      const job: Job<CrawlingJobData> = await crawlingService.scheduleJob(
        connectorType,
        scheduleConfig,
        orgId,
        userId,
      );

      logger.info('Crawling job scheduled successfully', {
        jobId: job.id,
        connectorType,
        orgId,
        userId,
      });

      res.status(HTTP_STATUS.CREATED).json({
        success: true,
        message: 'Crawling job scheduled successfully',
        data: {
          jobId: job.id,
          connectorType,
          scheduleConfig,
          scheduledAt: new Date(),
        },
      });
    } catch (error) {
      logger.error('Failed to schedule crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connectorType: req.params.connectorType,
      });
      next(error);
    }
  };

export const getJobStatus =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connectorType } = req.params;
    const { orgId } = req.user as { orgId: string };
    try {
      const jobStatus = await crawlingService.getJobStatus(
        connectorType as ConnectorType,
        orgId,
      );
      if (!jobStatus) {
        res.status(HTTP_STATUS.NOT_FOUND).json({
          success: false,
          message: 'No scheduled job found for this connector type',
        });
        return;
      }

      res.status(HTTP_STATUS.OK).json({
        success: true,
        data: jobStatus,
      });
    } catch (error) {
      logger.error('Failed to get job status', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connectorType: req.params.connectorType,
      });
      next(error);
    }
  };
  
  export const removeJob =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connectorType } = req.params;
    const { orgId } = req.user as { orgId: string };
    try {
      await crawlingService.removeJob(connectorType as ConnectorType, orgId);
      logger.info('Crawling job removed successfully', {
        connectorType,
        orgId,
      });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Crawling job removed successfully',
      });
    } catch (error) {
      logger.error('Failed to remove crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connectorType: req.params.connectorType,
      });
      next(error);
    }
  };
