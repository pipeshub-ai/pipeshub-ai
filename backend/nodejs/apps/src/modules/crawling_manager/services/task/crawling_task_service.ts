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
  /** Not attempted, and why (a snake_case reason): the worker's crawling_job.end says `outcome=skipped`. */
  skipped?: 'desktop_offline';
}