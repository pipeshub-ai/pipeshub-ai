import {
  CrawlingScheduleType,
  CrawlingStatus,
  FileFormatType,
} from './enums';
import { Types, Document } from 'mongoose';
import {
  ICustomCrawlingSchedule,
  IDailyCrawlingSchedule,
  IHourlyCrawlingSchedule,
  IMonthlyCrawlingSchedule,
  IOnceCrawlingSchedule,
  IWeeklyCrawlingSchedule,
} from './scheduler/scheduler';
import { JobProgress } from 'bullmq';

export interface CrawlingJobData {
  connector: string;
  scheduleConfig: ICrawlingSchedule;
  orgId: string;
  userId: string;
  timestamp: Date;
  metadata?: Record<string, any>;
}

export interface ScheduleJobOptions {
  priority?: number;
  maxRetries?: number;
  timeout?: number;
  metadata?: Record<string, any>;
}

export interface JobStatus {
  id: string | undefined;
  name: string;
  data: CrawlingJobData;
  progress: JobProgress;
  delay: number | undefined;
  timestamp: number;
  attemptsMade: number;
  finishedOn: number | undefined;
  processedOn: number | undefined;
  failedReason: string | undefined;
  state: string;
}

export interface IUserExclusionConfig {
  userId: Types.ObjectId;
  userEmail?: string;
  reason?: string;
  excludedAt: Date;
  excludedBy: Types.ObjectId;
}

export interface IUserGroupExclusionConfig {
  userGroupId: Types.ObjectId;
  userGroupName?: string;
  reason?: string;
  excludedAt: Date;
  excludedBy: Types.ObjectId;
}

export interface IFileFormatConfig {
  formatType: FileFormatType;
  extensions: string[];
  isEnabled: boolean;
  maxFileSizeBytes?: number;
  reason?: string;
}

// Discriminated union for all schedule configurations
export type ICrawlingSchedule =
  | ICustomCrawlingSchedule
  | IWeeklyCrawlingSchedule
  | IDailyCrawlingSchedule
  | IHourlyCrawlingSchedule
  | IMonthlyCrawlingSchedule
  | IOnceCrawlingSchedule;

// Interface for Crawling Statistics
export interface ICrawlingStats {
  totalRecordsProcessed: number;
  recordsAdded: number;
  recordsUpdated: number;
  recordsDeleted: number;
  recordsSkipped: number;
  recordsFailed: number;
  totalFilesProcessed: number;
  totalSizeProcessedBytes: number;
  averageProcessingTimeMs: number;
  lastRunDurationMs?: number;
  errorCount: number;
  lastError?: {
    message: string;
    timestamp: Date;
    connectorType?: string;
  };
}

// Main Crawling Manager Configuration Interface
export interface ICrawlingManagerConfig extends Document {
  orgId: Types.ObjectId;
  configName: string;
  description?: string;

  // User and Group Exclusions
  excludedUsers: IUserExclusionConfig[];
  excludedUserGroups: IUserGroupExclusionConfig[];

  // File Format Configuration
  fileFormatConfigs: IFileFormatConfig[];

  // Schedule Configuration
  crawlingSchedule: ICrawlingSchedule;

  // Control Settings
  isGloballyEnabled: boolean;
  maxConcurrentCrawlers: number;
  crawlTimeoutMinutes: number;
  retryAttempts: number;
  retryDelayMinutes: number;

  // Status and Control
  currentStatus: CrawlingStatus;
  statusMessage?: string;
  lastStatusUpdate: Date;

  // Time Controls
  startTime?: Date;
  stopTime?: Date;
  resumeTime?: Date;

  // Statistics
  crawlingStats: ICrawlingStats;

  // Metadata
  createdBy: Types.ObjectId;
  lastUpdatedBy: Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

// Type guards for schedule configurations
export function isCustomCrawlingSchedule(
  schedule: ICrawlingSchedule,
): schedule is ICustomCrawlingSchedule {
  return schedule.scheduleType === CrawlingScheduleType.CUSTOM;
}

export function isWeeklyCrawlingSchedule(
  schedule: ICrawlingSchedule,
): schedule is IWeeklyCrawlingSchedule {
  return schedule.scheduleType === CrawlingScheduleType.WEEKLY;
}

export function isDailyCrawlingSchedule(
  schedule: ICrawlingSchedule,
): schedule is IDailyCrawlingSchedule {
  return schedule.scheduleType === CrawlingScheduleType.DAILY;
}

export function isHourlyCrawlingSchedule(
  schedule: ICrawlingSchedule,
): schedule is IHourlyCrawlingSchedule {
  return schedule.scheduleType === CrawlingScheduleType.HOURLY;
}

export function isMonthlyCrawlingSchedule(
  schedule: ICrawlingSchedule,
): schedule is IMonthlyCrawlingSchedule {
  return schedule.scheduleType === CrawlingScheduleType.MONTHLY;
}

export function isOnceCrawlingSchedule(
  schedule: ICrawlingSchedule,
): schedule is IOnceCrawlingSchedule {
  return schedule.scheduleType === CrawlingScheduleType.ONCE;
}
