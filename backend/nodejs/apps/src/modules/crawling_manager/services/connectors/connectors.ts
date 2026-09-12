import { injectable, inject } from 'inversify';
import { Logger } from '../../../../libs/services/logger.service';
import {
  CrawlingResult,
  ICrawlingTaskService,
} from '../task/crawling_task_service';
import { SyncEventProducer } from '../../../knowledge_base/services/sync_events.service';
import { constructSyncConnectorEvent } from '../../utils/utils';
import { ICrawlingSchedule } from '../../schema/interface';
import { isLocalFsConnector } from '../../../../utils/local-fs-utils';
import { isLocalFsDesktopOnline } from '../../../../libs/services/desktop-presence.provider';

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
    connectorId: string,
  ): Promise<CrawlingResult> {
    this.logger.debug('Starting Connectors crawling', {
      orgId,
      userId,
      config,
      connector,
      connectorId,
    });

    try {
      // A Local FS pull needs the owner's desktop on the socket. Returning
      // success (not throwing) keeps BullMQ from retrying against a desktop
      // that is still offline; `null` (gateway not ready) publishes as usual.
      if (
        isLocalFsConnector(connector) &&
        isLocalFsDesktopOnline(orgId, userId, connectorId) === false
      ) {
        this.logger.info('Skipping scheduled Local FS sync: desktop offline', {
          orgId,
          userId,
          connectorId,
        });
        return { success: true };
      }

      const event = constructSyncConnectorEvent(orgId, connector, connectorId);

      await this.syncEventsService.publishEvent(event);

      this.logger.debug('Sync event published successfully', {
        orgId,
        connector,
        connectorId,
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
        connectorId,
      });
      throw error;
    }
  }
}
