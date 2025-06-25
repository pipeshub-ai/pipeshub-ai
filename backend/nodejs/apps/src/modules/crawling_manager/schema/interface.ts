import { IGoogleWorkspaceSettings } from './connectors/google_workspace';
import { IOneDriveSharePointSettings } from './connectors/one_drive';
import { ISlackSettings } from './connectors/slack';
import {
  ConnectorType,
  CrawlingScheduleType,
  CrawlingStatus,
  FileFormatType,
} from './enums';
import { Types, Document } from 'mongoose';

export interface IUserExclusionConfig {
  userId: Types.ObjectId;
  userEmail?: string;
  reason?: string;
  excludedAt: Date;
  excludedBy: Types.ObjectId;
}

// Interface for User Group Exclusion Configuration
export interface IUserGroupExclusionConfig {
  userGroupId: Types.ObjectId;
  userGroupName?: string;
  reason?: string;
  excludedAt: Date;
  excludedBy: Types.ObjectId;
}

// Interface for File Format Configuration
export interface IFileFormatConfig {
  formatType: FileFormatType;
  extensions: string[];
  isEnabled: boolean;
  maxFileSizeBytes?: number;
  reason?: string;
}

// Union type for all connector settings
type ConnectorSettings =
  | ISlackSettings
  | IGoogleWorkspaceSettings
  | IOneDriveSharePointSettings;

// Interface for Connector-specific Configuration
export interface IConnectorSpecificConfig {
  connectorType: ConnectorType;
  settings: ConnectorSettings;
  isEnabled: boolean;
  lastUpdatedBy: Types.ObjectId;
  updatedAt?: Date;
}

// Interface for Custom Schedule Configuration
export interface ICustomScheduleConfig {
  cronExpression: string;
  timezone?: string;
  description?: string;
}

// Interface for Weekly Schedule Configuration
export interface IWeeklyScheduleConfig {
  daysOfWeek: number[]; // 0-6 (Sunday-Saturday)
  hour: number; // 0-23
  minute: number; // 0-59
  timezone?: string;
}

// Interface for Daily Schedule Configuration
export interface IDailyScheduleConfig {
  hour: number; // 0-23
  minute: number; // 0-59
  timezone?: string;
}

// Interface for Hourly Schedule Configuration
export interface IHourlyScheduleConfig {
  minute: number; // 0-59
  interval?: number; // Every X hours (default: 1)
}

// Interface for Monthly Schedule Configuration
export interface IMonthlyScheduleConfig {
  dayOfMonth: number; // 1-31
  hour: number; // 0-23
  minute: number; // 0-59
  timezone?: string;
}

// Union type for schedule configurations
export type ScheduleConfig =
  | ICustomScheduleConfig
  | IWeeklyScheduleConfig
  | IDailyScheduleConfig
  | IHourlyScheduleConfig
  | IMonthlyScheduleConfig;

// Interface for Crawling Schedule
export interface ICrawlingSchedule {
  scheduleType: CrawlingScheduleType;
  scheduleConfig: ScheduleConfig;
  isEnabled: boolean;
  nextRunTime?: Date;
  lastRunTime?: Date;
  createdBy: Types.ObjectId;
  lastUpdatedBy: Types.ObjectId;
  createdAt?: Date;
  updatedAt?: Date;
}

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
    connectorType?: ConnectorType;
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

  // Connector-specific Configurations
  connectorConfigs: IConnectorSpecificConfig[];

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
