import { inject, injectable } from 'inversify';
import { ICrawlingTaskService } from './crawling_task_service';
import { Logger } from '../../../../libs/services/logger.service';
import { ConnectorsCrawlingService } from '../connectors/connectors';

@injectable()
export class CrawlingTaskFactory {
  private readonly logger: Logger;
  private readonly connectorsService: ConnectorsCrawlingService;
  constructor(
    @inject(ConnectorsCrawlingService)
    connectorsService: ConnectorsCrawlingService,
  ) {
    this.logger = Logger.getInstance({ service: 'CrawlingTaskFactory' });
    this.logger.info('CrawlingTaskFactory initialized');
    this.connectorsService = connectorsService;
  }

  getTaskService(): ICrawlingTaskService {
    return this.connectorsService;
  }
}
