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
import { isDesktopConnected } from '../../../../libs/services/desktop-presence.provider';

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
      schedule_type: config.scheduleType,
    });

    // A Local FS pull needs a desktop on the socket. Returning success (not
    // throwing) keeps BullMQ from retrying against a machine that is still
    // offline; unknown presence publishes as usual. Whether the *owner*
    // device is the one connected is deliberately not checked here: the job
    // has no request to authorize an instance lookup with, the relay only
    // ever routes the pull to the owner, and run_sync treats an offline
    // owner as a skipped sync rather than a failure.
    if (
      isLocalFsConnector(connector) &&
      isDesktopConnected(orgId, userId) === false
    ) {
      // The run's crawling_job.end reports it (outcome=skipped reason=desktop_offline).
      this.logger.debug('Skipping scheduled Local FS sync: no desktop connected');
      return { success: true, skipped: 'desktop_offline' };
    }

    const event = constructSyncConnectorEvent(orgId, connector, connectorId, userId);

    // A failure propagates unlogged: the worker's crawling_job.end reports the run once.
    await this.syncEventsService.publishEvent(event);

    this.logger.debug('Sync event published successfully');

    return {
      success: true,
    };
  }
}
