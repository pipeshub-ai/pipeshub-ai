import { ICrawlingSchedule } from "../../schema/interface";

export interface ICrawlingTaskService {
  crawl(
    orgId: string, 
    userId: string, 
    config: ICrawlingSchedule, 
    connector: string,
    connectorId: string,
  ): Promise<CrawlingResult>;
}

export interface CrawlingResult {
  success: boolean;
  error?: string;
  /** True when the job was a no-op (e.g. Local FS crawl with no connected watcher). */
  skipped?: boolean;
}