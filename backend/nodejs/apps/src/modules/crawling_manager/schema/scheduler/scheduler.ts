import { CrawlingScheduleType } from "../enums";
import { IBaseCrawlingSchedule } from "./base_scheduler";

// Schedule configuration interfaces
export interface ICustomScheduleConfig {
  cronExpression: string;
  timezone?: string;
  description?: string;
}

export interface IWeeklyScheduleConfig {
  daysOfWeek: number[]; // 0-6 (Sunday-Saturday)
  hour: number; // 0-23
  minute: number; // 0-59
  timezone?: string;
}

export interface IDailyScheduleConfig {
  hour: number; // 0-23
  minute: number; // 0-59
  timezone?: string;
}

export interface IHourlyScheduleConfig {
  minute: number; // 0-59
  interval?: number; // Every X hours (default: 1)
}

export interface IMonthlyScheduleConfig {
  dayOfMonth: number; // 1-31
  hour: number; // 0-23
  minute: number; // 0-59
  timezone?: string;
}

export interface IOnceScheduleConfig {
  scheduledTime: Date;
  timezone?: string;
}

// Specific interfaces for each schedule type
export interface ICustomCrawlingSchedule extends IBaseCrawlingSchedule {
  scheduleType: CrawlingScheduleType.CUSTOM;
  scheduleConfig: ICustomScheduleConfig;
}

export interface IWeeklyCrawlingSchedule extends IBaseCrawlingSchedule {
  scheduleType: CrawlingScheduleType.WEEKLY;
  scheduleConfig: IWeeklyScheduleConfig;
}

export interface IDailyCrawlingSchedule extends IBaseCrawlingSchedule {
  scheduleType: CrawlingScheduleType.DAILY;
  scheduleConfig: IDailyScheduleConfig;
}

export interface IHourlyCrawlingSchedule extends IBaseCrawlingSchedule {
  scheduleType: CrawlingScheduleType.HOURLY;
  scheduleConfig: IHourlyScheduleConfig;
}

export interface IMonthlyCrawlingSchedule extends IBaseCrawlingSchedule {
  scheduleType: CrawlingScheduleType.MONTHLY;
  scheduleConfig: IMonthlyScheduleConfig;
}

export interface IOnceCrawlingSchedule extends IBaseCrawlingSchedule {
  scheduleType: CrawlingScheduleType.ONCE;
  scheduleConfig: IOnceScheduleConfig;
}
