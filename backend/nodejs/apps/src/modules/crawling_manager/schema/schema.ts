import {
  ICustomCrawlingSchedule,
  IDailyCrawlingSchedule,
  IHourlyCrawlingSchedule,
  IMonthlyCrawlingSchedule,
  IOnceCrawlingSchedule,
  IWeeklyCrawlingSchedule,
} from './scheduler/scheduler';
import mongoose, { Model, Schema } from 'mongoose';
import {
  CrawlingScheduleType,
  CrawlingStatus,
  FileFormatType,
} from './enums';
import {
  IFileFormatConfig,
  IUserExclusionConfig,
  IUserGroupExclusionConfig,
  ICrawlingStats,
  ICrawlingManagerConfig,
} from './interface';

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

// Base schema for connector configurations
const BaseConnectorConfigSchema = new Schema(
  {
    connectorType: {
      type: String,
      required: true,
    },
    isEnabled: { type: Boolean, default: true },
    lastUpdatedBy: {
      type: Schema.Types.ObjectId,
      required: true,
      ref: 'users',
    },
    updatedAt: { type: Date, default: Date.now },
  },
  {
    discriminatorKey: 'connectorType',
    _id: false,
  },
);


// Base schema for schedule configurations
const BaseScheduleConfigSchema = new Schema(
  {
    scheduleType: {
      type: String,
      enum: Object.values(CrawlingScheduleType),
      required: true,
    },
  },
  {
    discriminatorKey: 'scheduleType',
    _id: false,
  },
);

// Base schema for crawling schedule (contains common fields)
const BaseCrawlingScheduleSchema = new Schema(
  {
    scheduleType: {
      type: String,
      enum: Object.values(CrawlingScheduleType),
      required: true,
    },
    scheduleConfig: {
      type: BaseScheduleConfigSchema,
      required: true,
    },
    isEnabled: { type: Boolean, default: true },
    nextRunTime: { type: Date },
    lastRunTime: { type: Date },
    createdBy: { type: Schema.Types.ObjectId, required: true, ref: 'users' },
    lastUpdatedBy: {
      type: Schema.Types.ObjectId,
      required: true,
      ref: 'users',
    },
  },
  {
    discriminatorKey: 'scheduleType',
    _id: false,
    timestamps: true,
  },
);

// Individual schedule configuration schemas
const CustomScheduleConfigSchema = new Schema(
  {
    cronExpression: {
      type: String,
      required: true,
      validate: {
        validator: function (v: string) {
          // Basic cron validation - can be enhanced with a proper cron library
          return /^(\S+\s+){4}\S+$/.test(v);
        },
        message: 'Invalid cron expression format',
      },
    },
    timezone: { type: String, default: 'UTC' },
    description: { type: String },
  },
  { _id: false },
);

const WeeklyScheduleConfigSchema = new Schema(
  {
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
  },
  { _id: false },
);

const DailyScheduleConfigSchema = new Schema(
  {
    hour: { type: Number, min: 0, max: 23, required: true },
    minute: { type: Number, min: 0, max: 59, required: true },
    timezone: { type: String, default: 'UTC' },
  },
  { _id: false },
);

const HourlyScheduleConfigSchema = new Schema(
  {
    minute: { type: Number, min: 0, max: 59, required: true },
    interval: { type: Number, min: 1, max: 24, default: 1 },
  },
  { _id: false },
);

const MonthlyScheduleConfigSchema = new Schema(
  {
    dayOfMonth: { type: Number, min: 1, max: 31, required: true },
    hour: { type: Number, min: 0, max: 23, required: true },
    minute: { type: Number, min: 0, max: 59, required: true },
    timezone: { type: String, default: 'UTC' },
  },
  { _id: false },
);

const OnceScheduleConfigSchema = new Schema(
  {
    scheduledTime: { type: Date, required: true },
    timezone: { type: String, default: 'UTC' },
  },
  { _id: false },
);

// Add discriminators for schedule config types
BaseScheduleConfigSchema.discriminator(
  CrawlingScheduleType.CUSTOM,
  CustomScheduleConfigSchema,
);

BaseScheduleConfigSchema.discriminator(
  CrawlingScheduleType.WEEKLY,
  WeeklyScheduleConfigSchema,
);

BaseScheduleConfigSchema.discriminator(
  CrawlingScheduleType.DAILY,
  DailyScheduleConfigSchema,
);

BaseScheduleConfigSchema.discriminator(
  CrawlingScheduleType.HOURLY,
  HourlyScheduleConfigSchema,
);

BaseScheduleConfigSchema.discriminator(
  CrawlingScheduleType.MONTHLY,
  MonthlyScheduleConfigSchema,
);

BaseScheduleConfigSchema.discriminator(
  CrawlingScheduleType.ONCE,
  OnceScheduleConfigSchema,
);

// Create discriminated schemas for each schedule type
const CustomCrawlingScheduleSchema = new Schema<ICustomCrawlingSchedule>({
  // scheduleConfig will be automatically handled by discriminator
});

const WeeklyCrawlingScheduleSchema = new Schema<IWeeklyCrawlingSchedule>({
  // scheduleConfig will be automatically handled by discriminator
});

const DailyCrawlingScheduleSchema = new Schema<IDailyCrawlingSchedule>({
  // scheduleConfig will be automatically handled by discriminator
});

const HourlyCrawlingScheduleSchema = new Schema<IHourlyCrawlingSchedule>({
  // scheduleConfig will be automatically handled by discriminator
});

const MonthlyCrawlingScheduleSchema = new Schema<IMonthlyCrawlingSchedule>({
  // scheduleConfig will be automatically handled by discriminator
});

const OnceCrawlingScheduleSchema = new Schema<IOnceCrawlingSchedule>({
  // scheduleConfig will be automatically handled by discriminator
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
      trim: true,
    },
    description: {
      type: String,
      maxLength: 500,
      trim: true,
    },

    // User and Group Exclusions
    excludedUsers: [UserExclusionConfigSchema],
    excludedUserGroups: [UserGroupExclusionConfigSchema],

    // File Format Configuration
    fileFormatConfigs: [FileFormatConfigSchema],

    // Schedule Configuration
    crawlingSchedule: {
      type: BaseCrawlingScheduleSchema,
      required: true,
    },

    // Control Settings
    isGloballyEnabled: { type: Boolean, default: true },
    maxConcurrentCrawlers: { type: Number, default: 5, min: 1, max: 20 },
    crawlTimeoutMinutes: { type: Number, default: 60, min: 1, max: 1440 },
    retryAttempts: { type: Number, default: 3, min: 0, max: 10 },
    retryDelayMinutes: { type: Number, default: 5, min: 1, max: 60 },

    // Status and Control
    currentStatus: {
      type: String,
      enum: Object.values(CrawlingStatus),
      default: CrawlingStatus.IDLE,
    },
    statusMessage: { type: String, maxLength: 1000 },
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
  },
);


// Add discriminators for schedule configurations
CrawlingManagerConfigSchema.discriminator(
  CrawlingScheduleType.CUSTOM,
  CustomCrawlingScheduleSchema,
);

CrawlingManagerConfigSchema.discriminator(
  CrawlingScheduleType.WEEKLY,
  WeeklyCrawlingScheduleSchema,
);

CrawlingManagerConfigSchema.discriminator(
  CrawlingScheduleType.DAILY,
  DailyCrawlingScheduleSchema,
);

CrawlingManagerConfigSchema.discriminator(
  CrawlingScheduleType.HOURLY,
  HourlyCrawlingScheduleSchema,
);

CrawlingManagerConfigSchema.discriminator(
  CrawlingScheduleType.MONTHLY,
  MonthlyCrawlingScheduleSchema,
);

CrawlingManagerConfigSchema.discriminator(
  CrawlingScheduleType.ONCE,
  OnceCrawlingScheduleSchema,
);

// Indexes for performance optimization
CrawlingManagerConfigSchema.index(
  { orgId: 1, configName: 1 },
  { unique: true },
);
CrawlingManagerConfigSchema.index({ currentStatus: 1 });
CrawlingManagerConfigSchema.index({ 'crawlingSchedule.nextRunTime': 1 });
CrawlingManagerConfigSchema.index({ 'crawlingSchedule.isEnabled': 1 });
CrawlingManagerConfigSchema.index({ isGloballyEnabled: 1 });
CrawlingManagerConfigSchema.index({ lastStatusUpdate: 1 });

// Validation middleware
CrawlingManagerConfigSchema.pre('save', function (next) {
  // Validate schedule configuration
  const schedule = this.crawlingSchedule;
  if (!Object.values(CrawlingScheduleType).includes(schedule.scheduleType)) {
    return next(new Error(`Invalid schedule type: ${schedule.scheduleType}`));
  }

  // Update lastStatusUpdate when status changes
  if (this.isModified('currentStatus')) {
    this.lastStatusUpdate = new Date();
  }

  next();
});


// Method to get next run time based on schedule
CrawlingManagerConfigSchema.methods.calculateNextRunTime =
  function (): Date | null {
    const schedule = this.crawlingSchedule;
    if (!schedule.isEnabled) return null;

    const now = new Date();

    switch (schedule.scheduleType) {
      case CrawlingScheduleType.ONCE:
        return schedule.scheduleConfig.scheduledTime > now
          ? schedule.scheduleConfig.scheduledTime
          : null;

      case CrawlingScheduleType.HOURLY:
        const nextHour = new Date(now);
        nextHour.setHours(
          nextHour.getHours() + (schedule.scheduleConfig.interval || 1),
        );
        nextHour.setMinutes(schedule.scheduleConfig.minute);
        nextHour.setSeconds(0);
        nextHour.setMilliseconds(0);
        return nextHour;

      // Add more schedule type calculations as needed
      default:
        return null;
    }
  };

// Export the Mongoose Model
export const CrawlingManagerConfig: Model<ICrawlingManagerConfig> =
  mongoose.model<ICrawlingManagerConfig>(
    'crawlingManagerConfig',
    CrawlingManagerConfigSchema,
    'crawlingManagerConfigs',
  );

export default {
  BaseScheduleConfigSchema,
  BaseConnectorConfigSchema,
  BaseCrawlingScheduleSchema,
  CustomCrawlingScheduleSchema,
  WeeklyCrawlingScheduleSchema,
  DailyCrawlingScheduleSchema,
  HourlyCrawlingScheduleSchema,
  MonthlyCrawlingScheduleSchema,
  OnceCrawlingScheduleSchema,
};