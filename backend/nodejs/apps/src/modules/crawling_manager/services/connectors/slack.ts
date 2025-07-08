import { injectable } from 'inversify';
import { Logger } from '../../../../libs/services/logger.service';
import {
  CrawlingResult,
  ICrawlingTaskService,
} from '../task/crawling_task_service';
import { ICrawlingSchedule } from '../../schema/interface';

@injectable()
export class SlackCrawlingService implements ICrawlingTaskService {
  private readonly logger: Logger;

  constructor() {
    this.logger = Logger.getInstance({ service: 'SlackCrawlingService' });
  }

  async crawl(
    orgId: string,
    userId: string,
    config: ICrawlingSchedule,
  ): Promise<CrawlingResult> {
    this.logger.info('Starting Slack crawling', { orgId, userId, config });

    try {
      // TODO: Implement Slack crawling logic
      this.logger.info('Slack crawling completed successfully', {
        orgId,
        userId,
      });
      return {
        success: true,
      };
    } catch (error) {
      this.logger.error('Slack crawling failed', {
        orgId,
        userId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }
}
