import { Schema } from 'mongoose';
import {
  ConnectorType,
  CrawlingScheduleType,
  CrawlingStatus,
  FileFormatType,
} from './enums';
import {
  IConnectorSpecificConfig,
  ICustomScheduleConfig,
  IDailyScheduleConfig,
  IFileFormatConfig,
  IHourlyScheduleConfig,
  IUserExclusionConfig,
  IUserGroupExclusionConfig,
  IWeeklyScheduleConfig,
  IMonthlyScheduleConfig,
  ICrawlingSchedule,
  ICrawlingStats,
  ICrawlingManagerConfig,
} from './interface';
import { OneDriveSharePointSettingsSchema } from './connectors/one_drive';
import { SlackSettingsSchema } from './connectors/slack';
import { GoogleDriveSettingsSchema } from './connectors/google_workspace';

// Schema for User Exclusion Configuration
const UserExclusionConfigSchema = new Schema<IUserExclusionConfig>({
  userId: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
  userEmail: { type: String },
  reason: { type: String },
  excludedAt: { type: Date, default: Date.now },
  excludedBy: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
});

// Schema for User Group Exclusion Configuration
const UserGroupExclusionConfigSchema = new Schema<IUserGroupExclusionConfig>({
  userGroupId: {
    type: Schema.Types.ObjectId,
    required: true,
    ref: 'userGroups',
  },
  userGroupName: { type: String },
  reason: { type: String },
  excludedAt: { type: Date, default: Date.now },
  excludedBy: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
});

// Schema for File Format Configuration
const FileFormatConfigSchema = new Schema<IFileFormatConfig>({
  formatType: {
    type: String,
    enum: Object.values(FileFormatType),
    required: true,
  },
  extensions: [{ type: String, required: true }],
  isEnabled: { type: Boolean, default: true },
  maxFileSizeBytes: { type: Number },
  reason: { type: String },
});

// Schema for Connector-specific Configuration
const ConnectorSpecificConfigSchema = new Schema<IConnectorSpecificConfig>({
  connectorType: {
    type: String,
    enum: Object.values(ConnectorType),
    required: true,
  },
  settings: {
    type: Schema.Types.Mixed,
    required: true,
  },
  isEnabled: { type: Boolean, default: true },
  lastUpdatedBy: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
  updatedAt: { type: Date, default: Date.now },
});

// Schema for Custom Schedule Configuration
// @ts-ignore - Temporarily ignoring unused declaration
const CustomScheduleConfigSchema = new Schema<ICustomScheduleConfig>({
  cronExpression: { type: String, required: true },
  timezone: { type: String, default: 'UTC' },
  description: { type: String },
});

// Schema for Weekly Schedule Configuration
// @ts-ignore - Temporarily ignoring unused declaration
const WeeklyScheduleConfigSchema = new Schema<IWeeklyScheduleConfig>({
  daysOfWeek: [
    {
      type: Number,
      min: 0,
      max: 6,
      required: true,
    },
  ],
  hour: { type: Number, min: 0, max: 23, required: true },
  minute: { type: Number, min: 0, max: 59, required: true },
  timezone: { type: String, default: 'UTC' },
});

// Schema for Daily Schedule Configuration
// @ts-ignore - Temporarily ignoring unused declaration
const DailyScheduleConfigSchema = new Schema<IDailyScheduleConfig>({
  hour: { type: Number, min: 0, max: 23, required: true },
  minute: { type: Number, min: 0, max: 59, required: true },
  timezone: { type: String, default: 'UTC' },
});

// Schema for Hourly Schedule Configuration
// @ts-ignore - Temporarily ignoring unused declaration
const HourlyScheduleConfigSchema = new Schema<IHourlyScheduleConfig>({
  minute: { type: Number, min: 0, max: 59, required: true },
  interval: { type: Number, min: 1, default: 1 },
});

// Schema for Monthly Schedule Configuration
// @ts-ignore - Temporarily ignoring unused declaration
const MonthlyScheduleConfigSchema = new Schema<IMonthlyScheduleConfig>({
  dayOfMonth: { type: Number, min: 1, max: 31, required: true },
  hour: { type: Number, min: 0, max: 23, required: true },
  minute: { type: Number, min: 0, max: 59, required: true },
  timezone: { type: String, default: 'UTC' },
});

// Schema for Crawling Schedule
const CrawlingScheduleSchema = new Schema<ICrawlingSchedule>({
  scheduleType: {
    type: String,
    enum: Object.values(CrawlingScheduleType),
    required: true,
  },
  scheduleConfig: {
    type: Schema.Types.Mixed,
    required: true,
    validate: {
      validator: function (value: any) {
        const scheduleType = (this as any).scheduleType;
        switch (scheduleType) {
          case CrawlingScheduleType.CUSTOM:
            return (
              value.cronExpression && typeof value.cronExpression === 'string'
            );
          case CrawlingScheduleType.WEEKLY:
            return (
              Array.isArray(value.daysOfWeek) &&
              typeof value.hour === 'number' &&
              typeof value.minute === 'number'
            );
          case CrawlingScheduleType.DAILY:
            return (
              typeof value.hour === 'number' && typeof value.minute === 'number'
            );
          case CrawlingScheduleType.HOURLY:
            return typeof value.minute === 'number';
          case CrawlingScheduleType.MONTHLY:
            return (
              typeof value.dayOfMonth === 'number' &&
              typeof value.hour === 'number' &&
              typeof value.minute === 'number'
            );
          default:
            return false;
        }
      },
      message: 'Invalid schedule configuration for the specified schedule type',
    },
  },
  isEnabled: { type: Boolean, default: true },
  nextRunTime: { type: Date },
  lastRunTime: { type: Date },
  createdBy: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
  lastUpdatedBy: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
});

// Schema for Crawling Statistics
const CrawlingStatsSchema = new Schema<ICrawlingStats>({
  totalRecordsProcessed: { type: Number, default: 0 },
  recordsAdded: { type: Number, default: 0 },
  recordsUpdated: { type: Number, default: 0 },
  recordsDeleted: { type: Number, default: 0 },
  recordsSkipped: { type: Number, default: 0 },
  recordsFailed: { type: Number, default: 0 },
  totalFilesProcessed: { type: Number, default: 0 },
  totalSizeProcessedBytes: { type: Number, default: 0 },
  averageProcessingTimeMs: { type: Number, default: 0 },
  lastRunDurationMs: { type: Number },
  errorCount: { type: Number, default: 0 },
  lastError: {
    message: { type: String },
    timestamp: { type: Date },
    connectorType: {
      type: String,
      enum: Object.values(ConnectorType),
    },
  },
});

// Main Crawling Manager Configuration Schema
const CrawlingManagerConfigSchema = new Schema<ICrawlingManagerConfig>(
  {
    orgId: {
      type: Schema.Types.ObjectId,
      required: true,
      ref: 'org',
      index: true,
    },
    configName: {
      type: String,
      required: true,
      maxLength: 100,
    },
    description: {
      type: String,
      maxLength: 500,
    },

    // User and Group Exclusions
    excludedUsers: [UserExclusionConfigSchema],
    excludedUserGroups: [UserGroupExclusionConfigSchema],

    // File Format Configuration
    fileFormatConfigs: [FileFormatConfigSchema],

    // Connector-specific Configurations
    connectorConfigs: [ConnectorSpecificConfigSchema],

    // Schedule Configuration
    crawlingSchedule: {
      type: CrawlingScheduleSchema,
      required: true,
    },

    // Control Settings
    isGloballyEnabled: { type: Boolean, default: true },
    maxConcurrentCrawlers: { type: Number, default: 5, min: 1, max: 20 },
    crawlTimeoutMinutes: { type: Number, default: 60, min: 1 },
    retryAttempts: { type: Number, default: 3, min: 0, max: 10 },
    retryDelayMinutes: { type: Number, default: 5, min: 1 },

    // Status and Control
    currentStatus: {
      type: String,
      enum: Object.values(CrawlingStatus),
      default: CrawlingStatus.IDLE,
    },
    statusMessage: { type: String },
    lastStatusUpdate: { type: Date, default: Date.now },

    // Time Controls
    startTime: { type: Date },
    stopTime: { type: Date },
    resumeTime: { type: Date },

    // Statistics
    crawlingStats: {
      type: CrawlingStatsSchema,
      default: () => ({}),
    },

    // Metadata
    createdBy: {
      type: Schema.Types.ObjectId,
      required: true,
      ref: 'users',
    },
    lastUpdatedBy: {
      type: Schema.Types.ObjectId,
      required: true,
      ref: 'users',
    },
  },
  {
    timestamps: true,
    collection: 'crawlingManagerConfigs',
  },
);

// Indexes for performance
CrawlingManagerConfigSchema.index(
  { orgId: 1, configName: 1 },
  { unique: true },
);
CrawlingManagerConfigSchema.index({ currentStatus: 1 });
CrawlingManagerConfigSchema.index({ 'crawlingSchedule.nextRunTime': 1 });
CrawlingManagerConfigSchema.index({ 'crawlingSchedule.isEnabled': 1 });
