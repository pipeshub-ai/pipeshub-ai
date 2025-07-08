import { ICrawlingSchedule } from "../../schema/interface";

export interface ICrawlingTaskService {
  crawl(orgId: string, userId: string, config: ICrawlingSchedule): Promise<CrawlingResult>;
}

export interface CrawlingResult {
  success: boolean;
  error?: string;
}