import { injectable, inject } from 'inversify';
import { Logger } from '../../../../libs/services/logger.service';
import {
  CrawlingResult,
  ICrawlingTaskService,
} from '../task/crawling_task_service';
import {
  SyncEventProducer,
} from '../../../knowledge_base/services/sync_events.service';
import { constructSyncConnectorEvent } from '../../utils/utils';
import { ICrawlingSchedule } from '../../schema/interface';

@injectable()
export class ConnectorsCrawlingService implements ICrawlingTaskService {
  private readonly logger: Logger;
  private readonly syncEventsService: SyncEventProducer;
  constructor(
    @inject('SyncEventProducer') syncEventsService: SyncEventProducer,
  ) {
    this.syncEventsService = syncEventsService;
    this.logger = Logger.getInstance({
      service: 'ConnectorsCrawlingService',
    });
  }

  async crawl(
    orgId: string,
    userId: string,
    config: ICrawlingSchedule,
    connector: string,
  ): Promise<CrawlingResult> {
    this.logger.info('Starting Connectors crawling', {
      orgId,
      userId,
      config,
      connector,
    });

    try {
      // TODO: Implement Connectors crawling logic
      this.logger.info('Connectors crawling completed successfully', {
        orgId,
        userId,
        connector,
      });

      // Construct the payload for the sync event using the connector information
      const event = constructSyncConnectorEvent(
        orgId,
        connector,
      );

      await this.syncEventsService.publishEvent(event);

      this.logger.info('Sync event published successfully', {
        orgId,
        connector,
      });

      return {
        success: true,
      };
    } catch (error) {
      this.logger.error('Connectors crawling failed', {
        orgId,
        userId,
        connector,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }
}
