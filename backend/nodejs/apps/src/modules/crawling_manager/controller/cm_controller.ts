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
    const { scheduleConfig, priority, maxRetries } = req.body;
    const { connectorType } = req.params as { connectorType: ConnectorType };
    const { userId, orgId } = req.user as { userId: string; orgId: string };

    try {
      logger.info('Scheduling crawling job', {
        connectorType,
        orgId,
        userId,
        scheduleType: scheduleConfig.scheduleType,
        isEnabled: scheduleConfig.isEnabled,
      });

      const job: Job<CrawlingJobData> = await crawlingService.scheduleJob(
        connectorType,
        scheduleConfig,
        orgId,
        userId,
        {
          priority,
          maxRetries,
        },
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
        orgId,
        userId,
      });
      next(error);
    }
  };

export const getCrawlingJobStatus =
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
          data: null,
        });
        return;
      }

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Job status retrieved successfully',
        data: jobStatus,
      });
    } catch (error) {
      logger.error('Failed to get job status', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connectorType: req.params.connectorType,
        orgId,
      });
      next(error);
    }
  };

export const removeCrawlingJob =
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
        orgId,
      });
      next(error);
    }
  };

export const getAllCrawlingJobStatus =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { orgId } = req.user as { orgId: string };

    try {
      const jobStatuses = await crawlingService.getAllJobs(orgId);

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'All job statuses retrieved successfully',
        data: jobStatuses,
      });
    } catch (error) {
      logger.error('Failed to get all crawling job statuses', {
        error: error instanceof Error ? error.message : 'Unknown error',
        orgId,
      });
      next(error);
    }
  };

export const removeAllCrawlingJob =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { orgId } = req.user as { orgId: string };

    try {
      await crawlingService.removeAllJobs(orgId);

      logger.info('All crawling jobs removed successfully', { orgId });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'All crawling jobs removed successfully',
      });
    } catch (error) {
      logger.error('Failed to remove all crawling jobs', {
        error: error instanceof Error ? error.message : 'Unknown error',
        orgId,
      });
      next(error);
    }
  };

export const pauseCrawlingJob =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connectorType } = req.params;
    const { orgId } = req.user as { orgId: string };

    try {
      await crawlingService.pauseJob(connectorType as ConnectorType, orgId);

      logger.info('Crawling job paused successfully', {
        connectorType,
        orgId,
      });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Crawling job paused successfully',
        data: {
          connectorType,
          orgId,
          pausedAt: new Date(),
        },
      });
    } catch (error) {
      logger.error('Failed to pause crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connectorType: req.params.connectorType,
        orgId,
      });
      next(error);
    }
  };

export const resumeCrawlingJob =
  (crawlingService: CrawlingSchedulerService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connectorType } = req.params;
    const { orgId } = req.user as { orgId: string };

    try {
      await crawlingService.resumeJob(connectorType as ConnectorType, orgId);

      logger.info('Crawling job resumed successfully', {
        connectorType,
        orgId,
      });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Crawling job resumed successfully',
        data: {
          connectorType,
          orgId,
          resumedAt: new Date(),
        },
      });
    } catch (error) {
      logger.error('Failed to resume crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connectorType: req.params.connectorType,
        orgId,
      });
      next(error);
    }
  };

export const getQueueStats =
  (crawlingService: CrawlingSchedulerService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const stats = await crawlingService.getQueueStats();

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Queue statistics retrieved successfully',
        data: stats,
      });
    } catch (error) {
      logger.error('Failed to get queue statistics', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      next(error);
    }
  };
