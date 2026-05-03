import { injectable, inject } from 'inversify';
import { Logger } from '../../../../libs/services/logger.service';
import {
  CrawlingResult,
  ICrawlingTaskService,
} from '../task/crawling_task_service';
import { SyncEventProducer } from '../../../knowledge_base/services/sync_events.service';
import { constructSyncConnectorEvent } from '../../utils/utils';
import { ICrawlingSchedule } from '../../schema/interface';
import { isLocalFsConnector } from '../../../knowledge_base/utils/local_fs_connector_name';

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
    this.logger.info('Starting Connectors crawling', {
      orgId,
      userId,
      config,
      connector,
      connectorId,
    });

    try {
      if (isLocalFsConnector(connector)) {
        // Local FS is client-managed: the desktop app runs its own scheduler
        // (see frontend electron/local-sync/manager.js scheduledTick). The
        // server-side BullMQ schedule has nothing to do here.
        this.logger.debug(
          'Skipping Local FS scheduled crawl — client-managed connector',
          { orgId, connector, connectorId },
        );
        return { success: true, skipped: true };
      }

      const event = constructSyncConnectorEvent(orgId, connector, connectorId);

      await this.syncEventsService.publishEvent(event);

      this.logger.info('Sync event published successfully', {
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
