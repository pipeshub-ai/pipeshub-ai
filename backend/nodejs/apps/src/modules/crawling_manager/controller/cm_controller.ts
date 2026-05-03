import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { CrawlingSchedulerService } from '../services/crawling_service';
import { Logger } from '../../../libs/services/logger.service';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { CrawlingJobData } from '../schema/interface';
import { Job } from 'bullmq';
import { BadRequestError, ForbiddenError, NotFoundError } from '../../../libs/errors/http.errors';
import { isUserAdmin } from '../../tokens_manager/controllers/connector.controllers';
import { executeConnectorCommand, handleBackendError } from '../../tokens_manager/utils/connector.utils';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AppConfig } from '../../tokens_manager/config/config';

const logger = Logger.getInstance({ service: 'CrawlingManagerController' });

export const handleConnectorResponse = (
  connectorResponse: any,
  operation: string,
  failureMessage: string,
) => {
  if (connectorResponse && connectorResponse.statusCode !== 200) {
    throw handleBackendError(connectorResponse, operation);
  }
  const connectorsData = connectorResponse.data;
  if (!connectorsData) {
    throw new NotFoundError(`${operation} failed: ${failureMessage}`);
  }
  return connectorsData.connector;
};

const validateConnectorAccess = async (req: AuthenticatedUserRequest, connectorId: string, appConfig: AppConfig) => {
  const { userId } = req.user as { userId: string };
  const isAdmin = await isUserAdmin(req);
  const headers: Record<string, string> = {
    ...(req.headers as Record<string, string>),
    'X-Is-Admin': isAdmin ? 'true' : 'false',
  };
  const connectorResponse = await executeConnectorCommand(
    `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
    HttpMethod.GET,
    headers,
  );
  const connector = handleConnectorResponse(connectorResponse, 'get connector instance' , 'Connector instance not found');

  if (connector.scope === 'team' && !isAdmin) {
    throw new ForbiddenError('You are not authorized to schedule this connector');
  }

  if (connector.scope === 'personal' && connector.createdBy !== userId) {
    throw new ForbiddenError('You are not authorized to schedule this connector');
  }
  return true;
};

export const scheduleCrawlingJob =
  (crawlingService: CrawlingSchedulerService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { scheduleConfig, priority, maxRetries } = req.body;
    const { connector, connectorId } = req.params as { connector: string; connectorId: string };
    const { userId, orgId } = req.user as { userId: string; orgId: string };

    try {
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      await validateConnectorAccess(req, connectorId, appConfig);

      logger.info('Scheduling crawling job', {
        connector,
        connectorId,
        orgId,
        userId,
        scheduleType: scheduleConfig.scheduleType,
        isEnabled: scheduleConfig.isEnabled,
      });

      const job: Job<CrawlingJobData> = await crawlingService.scheduleJob(
        connector,
        connectorId,
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
        connector,
        connectorId,
        orgId,
        userId,
      });

      res.status(HTTP_STATUS.CREATED).json({
        success: true,
        message: 'Crawling job scheduled successfully',
        data: {
          jobId: job.id,
          connector,
          scheduleConfig,
          scheduledAt: new Date(),
          connectorId,
        },
      });
    } catch (error) {
      logger.error('Failed to schedule crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connector: req.params.connector,
        connectorId,
        orgId,
        userId,
      });
      next(error);
    }
  };

export const getCrawlingJobStatus =
  (crawlingService: CrawlingSchedulerService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connector, connectorId } = req.params as { connector: string; connectorId: string };
    const { orgId } = req.user as { orgId: string };

    try {

      await validateConnectorAccess(req, connectorId, appConfig);
      const jobStatus = await crawlingService.getJobStatus(
        connector,
        connectorId,
        orgId,
      );

      if (!jobStatus) {
        res.status(HTTP_STATUS.NOT_FOUND).json({
          success: false,
          message: 'No scheduled job found for this connector',
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
        connector: req.params.connector,
        connectorId,
        orgId,
      });
      next(error);
    }
  };

export const removeCrawlingJob =
  (crawlingService: CrawlingSchedulerService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connector, connectorId } = req.params as { connector: string; connectorId: string };
    const { orgId } = req.user as { orgId: string };

    try {
      await validateConnectorAccess(req, connectorId, appConfig);
      await crawlingService.removeJob(connector, connectorId, orgId);

      logger.info('Crawling job removed successfully', {
        connector,
        connectorId,
        orgId,
      });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Crawling job removed successfully',
      });
    } catch (error) {
      logger.error('Failed to remove crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connector: req.params.connector,
        connectorId,
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
  (crawlingService: CrawlingSchedulerService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connector, connectorId } = req.params as { connector: string; connectorId: string };
    const { orgId } = req.user as { orgId: string };

    try {
      await validateConnectorAccess(req, connectorId, appConfig);
      await crawlingService.pauseJob(connector, connectorId, orgId);

      logger.info('Crawling job paused successfully', {
        connector,
        connectorId,
        orgId,
      });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Crawling job paused successfully',
        data: {
          connector,
          orgId,
          pausedAt: new Date(),
        },
      });
    } catch (error) {
      logger.error('Failed to pause crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connector: req.params.connector,
        connectorId,
        orgId,
      });
      next(error);
    }
  };

export const resumeCrawlingJob =
  (crawlingService: CrawlingSchedulerService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const { connector, connectorId } = req.params as { connector: string; connectorId: string };
    const { orgId } = req.user as { orgId: string };

    try {
      await validateConnectorAccess(req, connectorId, appConfig);
      await crawlingService.resumeJob(connector, connectorId, orgId);

      logger.info('Crawling job resumed successfully', {
        connector,
        connectorId,
        orgId,
      });

      res.status(HTTP_STATUS.OK).json({
        success: true,
        message: 'Crawling job resumed successfully',
        data: {
          connector,
          orgId,
          resumedAt: new Date(),
        },
      });
    } catch (error) {
      logger.error('Failed to resume crawling job', {
        error: error instanceof Error ? error.message : 'Unknown error',
        connector: req.params.connector,
        connectorId,
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
