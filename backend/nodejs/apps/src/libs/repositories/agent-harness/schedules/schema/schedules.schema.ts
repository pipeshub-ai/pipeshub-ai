import { Model, Schema } from 'mongoose';
import { harnessModel } from '../../schema/harness-model.schema';
import type {
  ScheduleDocument,
  ScheduleCounterDocument,
  ScheduleFiringDocument,
} from '../document';

export type {
  ScheduleDocument,
  ScheduleCounterDocument,
  ScheduleFiringDocument,
} from '../document';

export const SCHEDULES_BY_ACTOR_INDEX = 'org_actor_created';
export const SCHEDULES_BY_ORG_INDEX = 'org_created';
export const SCHEDULES_DUE_INDEX = 'status_next_fire';
export const SCHEDULES_BY_TRIGGER_INDEX = 'trigger_kind_status_org';
export const FIRINGS_BY_SLOT_INDEX = 'org_schedule_slot';
export const FIRINGS_BY_SCHEDULE_INDEX = 'org_schedule_slot_at';
export const FIRINGS_PENDING_INDEX = 'status_lease';
export const FIRINGS_WATCHED_INDEX = 'status_watch';
export const FIRINGS_WATCH_ROUND_INDEX = 'status_watch_checked';
export const COUNTERS_EXPIRY_INDEX = 'expires_at';

const scheduleSchema = new Schema<ScheduleDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    actorId: { type: String, required: true },
    name: { type: String, required: true },
    agentKey: { type: String, default: undefined },
    // A version number or 'latest'.
    agentVersion: { type: Schema.Types.Mixed, default: undefined },
    input: { type: Schema.Types.Mixed, default: null },
    trigger: { type: Schema.Types.Mixed, required: true },
    maxCostUsd: { type: Number, default: undefined },
    deadlineS: { type: Number, required: true },
    status: {
      type: String,
      // `deleted`: a tombstone (ScheduleTombstoneDocument), never read as a schedule.
      enum: ['active', 'paused', 'disabled', 'deleted'],
      required: true,
    },
    statusReason: { type: String, default: null },
    nextFireAt: { type: Number, default: null },
    webhookTokenHash: { type: String, default: null },
    webhookSigningKey: { type: Schema.Types.Mixed, default: undefined },
    overlap: {
      type: String,
      enum: ['allow', 'skip', 'queue'],
      default: undefined,
    },
    notify: { type: [String], default: undefined },
    origin: { type: Schema.Types.Mixed, default: undefined },
    revision: { type: Number, default: undefined },
    lastChangedFrom: { type: Schema.Types.Mixed, default: undefined },
    skipNextAt: { type: Number, default: undefined },
    codingEngine: { type: String, default: undefined },
    deletedAt: { type: Number, default: undefined },
    createdAt: { type: Number, required: true },
    updatedAt: { type: Number, required: true },
  },
  {
    collection: 'agentSchedules',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
scheduleSchema.index(
  { orgId: 1, actorId: 1, createdAt: -1 },
  { name: SCHEDULES_BY_ACTOR_INDEX },
);
scheduleSchema.index(
  { orgId: 1, createdAt: -1 },
  { name: SCHEDULES_BY_ORG_INDEX },
);
scheduleSchema.index(
  { status: 1, nextFireAt: 1 },
  { name: SCHEDULES_DUE_INDEX },
);
scheduleSchema.index(
  { 'trigger.kind': 1, status: 1, orgId: 1 },
  { name: SCHEDULES_BY_TRIGGER_INDEX },
);

const firingSchema = new Schema<ScheduleFiringDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    scheduleId: { type: String, required: true },
    actorId: { type: String, required: true },
    slotKey: { type: String, required: true },
    slotAt: { type: Number, required: true },
    kind: {
      type: String,
      enum: ['cron', 'once', 'manual', 'event'],
      required: true,
    },
    status: {
      type: String,
      enum: ['pending', 'admitted', 'refused', 'skipped'],
      required: true,
    },
    runId: { type: String, default: null },
    sessionId: { type: String, default: null },
    reason: { type: String, default: null },
    missed: { type: Number, default: 0 },
    attempts: { type: Number, default: 0 },
    leaseUntil: { type: Number, required: true },
    createdAt: { type: Number, required: true },
    settledAt: { type: Number, default: null },
    watchUntil: { type: Number, default: null },
    notifiedAt: { type: Number, default: null },
    watchCheckedAt: { type: Number, default: undefined },
    event: { type: Schema.Types.Mixed, default: undefined },
    context: { type: Schema.Types.Mixed, default: undefined },
    queuedBehind: { type: String, default: undefined },
    watchEndedAt: { type: Number, default: undefined },
    runner: { type: String, enum: ['harness', 'classic'], default: undefined },
    runRef: { type: String, default: undefined },
    costCapIgnored: { type: Boolean, default: undefined },
  },
  {
    collection: 'agentScheduleFirings',
    versionKey: false,
    autoIndex: false,
    minimize: false,
  },
);
firingSchema.index(
  { orgId: 1, scheduleId: 1, slotKey: 1 },
  { name: FIRINGS_BY_SLOT_INDEX, unique: true },
);
firingSchema.index(
  { orgId: 1, scheduleId: 1, slotAt: -1 },
  { name: FIRINGS_BY_SCHEDULE_INDEX },
);
firingSchema.index(
  { status: 1, leaseUntil: 1 },
  { name: FIRINGS_PENDING_INDEX },
);
firingSchema.index(
  { status: 1, watchUntil: 1 },
  { name: FIRINGS_WATCHED_INDEX },
);
firingSchema.index(
  { status: 1, watchCheckedAt: 1 },
  { name: FIRINGS_WATCH_ROUND_INDEX },
);

export const AgentScheduleModel: Model<ScheduleDocument> =
  harnessModel<ScheduleDocument>('AgentSchedule', scheduleSchema);

export const AgentScheduleFiringModel: Model<ScheduleFiringDocument> =
  harnessModel<ScheduleFiringDocument>('AgentScheduleFiring', firingSchema);

const counterSchema = new Schema<ScheduleCounterDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    count: { type: Number, required: true },
    updatedAt: { type: Number, required: true },
    expiresAt: { type: Date, default: undefined },
  },
  {
    collection: 'agentScheduleCounters',
    versionKey: false,
    autoIndex: false,
  },
);
counterSchema.index(
  { expiresAt: 1 },
  { name: COUNTERS_EXPIRY_INDEX, expireAfterSeconds: 0 },
);

export const AgentScheduleCounterModel: Model<ScheduleCounterDocument> =
  harnessModel<ScheduleCounterDocument>('AgentScheduleCounter', counterSchema);
