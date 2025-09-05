import { inject, injectable } from "inversify";
import { ConnectorType } from "../../schema/enums";
import { ICrawlingTaskService } from "./crawling_task_service";
import { GoogleWorkspaceCrawlingService } from "../connectors/google_workspace";
import { SlackCrawlingService } from "../connectors/slack";
import { S3CrawlingService } from "../connectors/s3";
import { Logger } from "../../../../libs/services/logger.service";

@injectable()           
export class CrawlingTaskFactory {
  private readonly logger: Logger;
  constructor(
    @inject(GoogleWorkspaceCrawlingService) private googleWorkspaceService: GoogleWorkspaceCrawlingService,
    @inject(SlackCrawlingService) private slackService: SlackCrawlingService,
    @inject(S3CrawlingService) private s3Service: S3CrawlingService,
  ) {
    this.logger = Logger.getInstance({ service: 'CrawlingTaskFactory' });
    this.logger.info('CrawlingTaskFactory initialized');
  }

  getTaskService(connectorType: ConnectorType): ICrawlingTaskService {
    switch (connectorType) {
      case ConnectorType.GOOGLE_WORKSPACE:
        return this.googleWorkspaceService;
      
      case ConnectorType.SLACK:
        return this.slackService;
      
      case ConnectorType.S3:
        return this.s3Service;
      
      // Add other connector types as needed
      case ConnectorType.ONE_DRIVE:
      case ConnectorType.SHAREPOINT_ONLINE:
      case ConnectorType.CONFLUENCE:
      case ConnectorType.AZURE_BLOB_STORAGE:
        throw new Error(`Connector type ${connectorType} not yet implemented`);
      
      default:
        throw new Error(`Unknown connector type: ${connectorType}`);
    }
  }
}
