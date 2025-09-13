import { z } from 'zod';
import { CrawlingScheduleType } from '../schema/enums';

// Base Schedule Configuration Schemas
const BaseScheduleConfigSchema = z.object({
  scheduleType: z.nativeEnum(CrawlingScheduleType),
  isEnabled: z.boolean().default(true),
  timezone: z.string().default('UTC'),
});

const HourlyScheduleConfigSchema = BaseScheduleConfigSchema.extend({
  scheduleType: z.literal(CrawlingScheduleType.HOURLY),
  minute: z.number().min(0).max(59),
  interval: z.number().min(1).max(24).default(1),
});

const DailyScheduleConfigSchema = BaseScheduleConfigSchema.extend({
  scheduleType: z.literal(CrawlingScheduleType.DAILY),
  hour: z.number().min(0).max(23),
  minute: z.number().min(0).max(59),
});

const WeeklyScheduleConfigSchema = BaseScheduleConfigSchema.extend({
  scheduleType: z.literal(CrawlingScheduleType.WEEKLY),
  daysOfWeek: z.array(z.number().min(0).max(6)).min(1),
  hour: z.number().min(0).max(23),
  minute: z.number().min(0).max(59),
});

const MonthlyScheduleConfigSchema = BaseScheduleConfigSchema.extend({
  scheduleType: z.literal(CrawlingScheduleType.MONTHLY),
  dayOfMonth: z.number().min(1).max(31),
  hour: z.number().min(0).max(23),
  minute: z.number().min(0).max(59),
});

const CustomScheduleConfigSchema = BaseScheduleConfigSchema.extend({
  scheduleType: z.literal(CrawlingScheduleType.CUSTOM),
  cronExpression: z
    .string()
    .regex(/^(\S+\s+){4}\S+$/, 'Invalid cron expression format'),
  description: z.string().optional(),
});

const OnceScheduleConfigSchema = BaseScheduleConfigSchema.extend({
  scheduleType: z.literal(CrawlingScheduleType.ONCE),
  scheduledTime: z.string().datetime(),
});

// Discriminated Union for Schedule Config
const ScheduleConfigSchema = z.discriminatedUnion('scheduleType', [
  HourlyScheduleConfigSchema,
  DailyScheduleConfigSchema,
  WeeklyScheduleConfigSchema,
  MonthlyScheduleConfigSchema,
  CustomScheduleConfigSchema,
  OnceScheduleConfigSchema,
]);

// connector type schema
export const ConnectorTypeSchema = z.object({
  params: z.object({
    connector: z.string(),
  }),
});

// Main API Request Schema
export const CrawlingScheduleRequestSchema = z.object({
  params: z.object({
    connector: z.string(),
  }),
  body: z.object({
    scheduleConfig: ScheduleConfigSchema,
    priority: z.number().min(1).max(10).default(5),
    maxRetries: z.number().min(0).max(10).default(3),
    timeout: z.number().min(1000).max(600000).default(300000), // 5 minutes default
  }),
  headers: z.object({
    authorization: z.string(),
  }),
});

// Job Status Request Schema
export const JobStatusRequestSchema = z.object({
  params: z.object({
    connector: z.string(),
  }),
  headers: z.object({
    authorization: z.string(),
  }),
});
