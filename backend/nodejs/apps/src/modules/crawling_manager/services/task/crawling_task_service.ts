import { ICrawlingSchedule } from "../../schema/interface";

export interface ICrawlingTaskService {
  crawl(
    orgId: string, 
    userId: string, 
    config: ICrawlingSchedule, 
    connector: string,
  ): Promise<CrawlingResult>;
}

export interface CrawlingResult {
  success: boolean;
  error?: string;
}